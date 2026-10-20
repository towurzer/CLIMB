"""
SigLIP2 embedding of keyframes.

Work is found by "keyframes with no row in keyframe_embedding for this model", which makes the
stage naturally resumable and idempotent. Multi-host sharding is `mod(keyframe_id, shards)`:
deterministic, needs no coordination and no claims table, so multiple GPUs can be pointed at the
same database and will not collide.

Reading keyframes off disk overlaps with the model: CLIMB_EMBED_LOADERS threads decode the next
batches while this one is embedded, and resizing and normalising happens in the image processor on
the model's device. Done one after the other, decoding and preprocessing alone capped the stage at
about 160 keyframes/s on the laptop, whatever the GPU.
"""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import custom_logger
from config import Config
from pipeline import device, paths, visual_models
from psycopg2.extras import execute_values
from tqdm import tqdm

SELECT_PENDING = """
    SELECT k.keyframe_id, k.video_id, s.shot_index, k.kf_index
    FROM keyframes k
             JOIN scenes s ON s.scene_id = k.scene_id
             JOIN videos v ON v.video_id = k.video_id
    WHERE NOT EXISTS (SELECT 1
                      FROM keyframe_embedding e
                      WHERE e.keyframe_id = k.keyframe_id
                        AND e.model_id = %(model_id)s)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(k.keyframe_id, %(shards)s) = %(shard)s
      AND k.keyframe_id > %(after)s
    ORDER BY k.keyframe_id
    LIMIT %(limit)s;
"""

COUNT_PENDING = """
    SELECT count(*)
    FROM keyframes k
             JOIN videos v ON v.video_id = k.video_id
    WHERE NOT EXISTS (SELECT 1
                      FROM keyframe_embedding e
                      WHERE e.keyframe_id = k.keyframe_id
                        AND e.model_id = %(model_id)s)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(k.keyframe_id, %(shards)s) = %(shard)s;
"""

INSERT_EMBEDDINGS = """
    INSERT INTO keyframe_embedding (keyframe_id, model_id, embedding)
    VALUES %s
    ON CONFLICT (keyframe_id, model_id) DO NOTHING
"""

SELECT_MODEL = "SELECT model_id, name, dims FROM embedding_model WHERE name = %s;"

COUNT_VECTORS = """
    SELECT m.name, count(*)
    FROM keyframe_embedding e
             JOIN embedding_model m USING (model_id)
    GROUP BY m.name;
"""


@dataclass
class EmbedResult:
    embedded: int = 0
    skipped: int = 0
    batches: int = 0


def to_pgvector(values) -> str:
    # tolist() first: formatting Python floats is 1.6x faster than numpy scalars, same text.
    return "[" + ",".join(f"{v:.6f}" for v in values.tolist()) + "]"


def load_model(model_name: str, dev: str):
    """(image processor, vision encoder) for any visual model, see pipeline/visual_models.py."""
    return visual_models.load_image_side(model_name, dev)


def decode_keyframe(path):
    """
    A keyframe as a uint8 (channels, height, width) tensor, for the image processor to resize and
    normalise. Gives the same pixel values as handing it the PIL image, and leaves the work that
    holds the GIL to the device; decoding alone runs in parallel threads.
    """
    import numpy as np
    import torch
    from PIL import Image

    with Image.open(path) as image:
        return torch.from_numpy(np.array(image.convert("RGB"))).permute(2, 0, 1)


def _load_batch(rows):
    """(keyframe ids, images, [(keyframe id, path, error)]) for one batch of pending rows."""
    ids, images, failed = [], [], []
    for keyframe_id, video_id, shot_index, kf_index in rows:
        path = paths.keyframe_path(video_id, shot_index, kf_index)
        try:
            images.append(decode_keyframe(path))
            ids.append(keyframe_id)
        except Exception as e:
            failed.append((keyframe_id, path, e))
    return ids, images, failed


def embed_images(images, processor, vision, dev):
    import torch

    inputs = processor(images=images, return_tensors="pt", device=dev).to(dev)
    with torch.no_grad():
        features = vision(**inputs).pooler_output
        features = features / features.norm(p=2, dim=-1, keepdim=True)
    return features.float().cpu().numpy()


def resolve_model(conn, model_name: str):
    with conn.cursor() as cur:
        cur.execute(SELECT_MODEL, (model_name,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(
            f"Model '{model_name}' is not registered in embedding_model. "
            f"Add it in a migration before embedding with it."
        )
    return row


COLLECTION_COVERAGE = """
    SELECT count(*), count(e.keyframe_id)
    FROM keyframes k
             JOIN videos v ON v.video_id = k.video_id
             LEFT JOIN keyframe_embedding e
                       ON e.keyframe_id = k.keyframe_id
                           AND e.model_id = (SELECT model_id FROM embedding_model WHERE name = %s)
    WHERE v.collection = %s;
"""


def collection_coverage(conn, model_name, collection) -> tuple:
    """(keyframes of the collection, how many of them have a vector from the model)."""
    with conn.cursor() as cur:
        cur.execute(COLLECTION_COVERAGE, (model_name, collection))
        return cur.fetchone()


def vector_counts(conn) -> tuple:
    """({model name: keyframes with a vector from it}, keyframes in total)."""
    with conn.cursor() as cur:
        cur.execute(COUNT_VECTORS)
        counts = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM keyframes;")
        return counts, cur.fetchone()[0]


def embed_jobs(model_name=None, collection=None) -> list:
    """
    (model, collection) pairs to embed with, in order. Without a model: every model in
    Config.embed_models() for `collection` (all of them when None), then each domain model for its
    own collection, unless `collection` names another. A domain model named on its own embeds its
    own collection unless told otherwise.
    """
    conf = Config()
    if model_name:
        return [(model_name, collection or conf.collection_of(model_name))]
    jobs = [(name, collection) for name in conf.embed_models()]
    jobs += [(name, owner) for owner, name in conf.collection_models().items()
             if collection is None or collection == owner]
    return jobs


def embed_pending(conn, model_name=None, collection=None, batch_size=None, limit=None,
                  shard=0, shards=1, prefer_device=None) -> EmbedResult:
    """
    Embeds the pending keyframes for every job of embed_jobs(), one after the other. `limit`
    applies to each job.
    """
    total = EmbedResult()
    for name, job_collection in embed_jobs(model_name, collection):
        result = _embed_with(conn, name, job_collection, batch_size, limit, shard, shards,
                             prefer_device)
        total.embedded += result.embedded
        total.skipped += result.skipped
        total.batches += result.batches
    return total


def _embed_with(conn, model_name, collection, batch_size, limit, shard, shards,
                prefer_device) -> EmbedResult:
    conf = Config()
    logger = custom_logger.get_logger("embed")
    batch_size = batch_size or conf.EMBEDDING_BATCH_SIZE

    model_id, registered_name, dims = resolve_model(conn, model_name)
    params = {"model_id": model_id, "collection": collection, "shard": shard, "shards": shards}

    with conn.cursor() as cur:
        cur.execute(COUNT_PENDING, params)
        pending = cur.fetchone()[0]

    if pending == 0:
        logger.info(f"No keyframes pending for {registered_name}.")
        return EmbedResult()

    dev = device.pick_device(prefer_device)
    device.log_device("embed", dev)
    logger.info(
        f"Embedding {pending} keyframe(s) with {registered_name} ({dims}d), "
        f"batch {batch_size}" + (f", shard {shard}/{shards}" if shards > 1 else "")
    )

    processor, vision = load_model(registered_name, dev)
    result = EmbedResult()
    progress_total = min(pending, limit) if limit else pending
    loaders = max(1, conf.EMBED_LOADERS)

    # Pending rows are read in keyframe_id order from where the last batch ended, not re-queried
    # from the start. The next batches are read and decoding before this one is written, so a
    # fresh query would hand them out twice; and a keyframe whose file is missing is still
    # pending, so a fresh query also returned it first every time, and a run of them filling a
    # batch stalled the stage.
    after, taken = -1, 0
    in_flight = deque()

    def submit_next():
        nonlocal after, taken
        take = min(batch_size, progress_total - taken)
        if take <= 0:
            return
        with conn.cursor() as cur:
            cur.execute(SELECT_PENDING, {**params, "after": after, "limit": take})
            rows = cur.fetchall()
        if rows:
            after, taken = rows[-1][0], taken + len(rows)
            in_flight.append((len(rows), pool.submit(_load_batch, rows)))

    with ThreadPoolExecutor(loaders, thread_name_prefix="embed-load") as pool, \
            tqdm(total=progress_total, desc="Embedding keyframes", unit="keyframe") as progress:
        for _ in range(2 * loaders):
            submit_next()
        while in_flight:
            count, loading = in_flight.popleft()
            ids, images, failed = loading.result()
            submit_next()
            for keyframe_id, path, e in failed:
                # A missing keyframe file must not stall the stage; it stays pending and will be
                # picked up again once selection has been re-run for that video.
                logger.warning(f"keyframe {keyframe_id}: cannot read {path} ({e})")
            result.skipped += len(failed)

            if images:
                vectors = embed_images(images, processor, vision, dev)
                if vectors.shape[1] != dims:
                    raise ValueError(
                        f"{registered_name} produced {vectors.shape[1]} dimensions, "
                        f"but embedding_model says {dims}"
                    )
                payload = [(kid, model_id, to_pgvector(v)) for kid, v in zip(ids, vectors)]
                with conn.cursor() as cur:
                    execute_values(cur, INSERT_EMBEDDINGS, payload, page_size=500)
                conn.commit()
                result.embedded += len(payload)
                result.batches += 1

            progress.update(count)

    logger.info(f"Embedded {result.embedded} keyframe(s), skipped {result.skipped}")
    return result
