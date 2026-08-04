"""
SigLIP2 embedding of keyframes.

Work is found by "keyframes with no row in keyframe_embedding for this model", which makes the
stage naturally resumable and idempotent. Multi-host sharding is `mod(keyframe_id, shards)`:
deterministic, needs no coordination and no claims table, so multiple GPUs can be pointed at the
same database and will not collide.
"""

from dataclasses import dataclass

import custom_logger
from config import Config
from pipeline import device, paths
from psycopg2.extras import execute_values

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


@dataclass
class EmbedResult:
    embedded: int = 0
    skipped: int = 0
    batches: int = 0


def to_pgvector(values) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


def load_model(model_name: str, dev: str):
    """
    Loads the full checkpoint and keeps the vision tower.

    Loading `Siglip2VisionModel` directly looks tempting -- it would skip the text weights -- but
    this checkpoint's config type is `siglip_vision_model`, not `siglip2_vision_model`, so
    transformers reports mismatched patch and position embeddings and offers to reinitialise them
    at random. It currently raises instead, but relying on that is one `ignore_mismatched_sizes`
    default away from silently embedding 12M keyframes with random projection weights. Loading the
    whole model and discarding the text tower is a few seconds and some transient memory; the
    alternative failure is invisible.
    """
    from transformers import AutoModel, AutoProcessor

    processor = AutoProcessor.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name, dtype=device.pick_dtype(dev))
    vision = model.vision_model.to(dev).eval()
    return processor, vision


def embed_images(images, processor, vision, dev):
    import torch

    inputs = processor(images=images, return_tensors="pt").to(dev)
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


def embed_pending(conn, model_name=None, collection=None, batch_size=None, limit=None,
                  shard=0, shards=1, prefer_device=None) -> EmbedResult:
    conf = Config()
    logger = custom_logger.get_logger("embed")
    model_name = model_name or conf.KIS_MODEL_NAME
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
    remaining = limit if limit else pending

    while remaining > 0:
        take = min(batch_size, remaining)
        with conn.cursor() as cur:
            cur.execute(SELECT_PENDING, {**params, "limit": take})
            rows = cur.fetchall()
        if not rows:
            break

        images, ids = [], []
        for keyframe_id, video_id, shot_index, kf_index in rows:
            path = paths.keyframe_path(video_id, shot_index, kf_index)
            try:
                from PIL import Image
                images.append(Image.open(path).convert("RGB"))
                ids.append(keyframe_id)
            except Exception as e:
                # A missing keyframe file must not stall the stage; it stays pending and will be
                # picked up again once selection has been re-run for that video.
                logger.warning(f"keyframe {keyframe_id}: cannot read {path} ({e})")
                result.skipped += 1

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

        remaining -= len(rows)
        if result.batches and result.batches % 20 == 0:
            logger.info(f"  {result.embedded}/{pending} embedded")

    logger.info(f"Embedded {result.embedded} keyframe(s), skipped {result.skipped}")
    return result
