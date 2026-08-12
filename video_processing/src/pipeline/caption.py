"""
VLM captions over keyframes.

Embeddings flatten composition -- "a man in a red hat next to a bicycle" and "a man next to a red
bicycle" land in nearly the same place. A generated caption keeps the relations as words, which
the text index can then match exactly. Scope is Config.CAPTION_SCOPE, defaulting to one
keyframe per shot: a description of a shot changes little between its own keyframes.
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
    WHERE (%(scope_all)s OR k.kf_index = 0)
      AND NOT EXISTS (SELECT 1 FROM keyframe_caption c WHERE c.keyframe_id = k.keyframe_id)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(k.keyframe_id, %(shards)s) = %(shard)s
    ORDER BY k.keyframe_id
    LIMIT %(limit)s;
"""

COUNT_PENDING = """
    SELECT count(*)
    FROM keyframes k
             JOIN videos v ON v.video_id = k.video_id
    WHERE (%(scope_all)s OR k.kf_index = 0)
      AND NOT EXISTS (SELECT 1 FROM keyframe_caption c WHERE c.keyframe_id = k.keyframe_id)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(k.keyframe_id, %(shards)s) = %(shard)s;
"""

INSERT_CAPTIONS = """
    INSERT INTO keyframe_caption (keyframe_id, caption, model)
    VALUES %s
    ON CONFLICT (keyframe_id) DO NOTHING
"""


@dataclass
class CaptionResult:
    captioned: int = 0
    skipped: int = 0


def load_model(dev: str):
    """Lazily loads the captioning VLM."""
    conf = Config()
    try:
        from transformers import AutoModelForImageTextToText, AutoProcessor
    except ImportError as e:
        raise ImportError("transformers is required for captioning") from e

    processor = AutoProcessor.from_pretrained(conf.CAPTION_MODEL)
    # See Config.CAPTION_IMAGE_SPLITTING: tiling a 480x270 keyframe into 512px crops costs 5.3x
    # for no detail that was not already there. Set only where the processor supports it.
    image_processor = getattr(processor, "image_processor", None)
    if image_processor is not None and hasattr(image_processor, "do_image_splitting"):
        image_processor.do_image_splitting = conf.CAPTION_IMAGE_SPLITTING
    model = AutoModelForImageTextToText.from_pretrained(
        conf.CAPTION_MODEL, dtype=device.pick_dtype(dev)
    ).to(dev).eval()
    return processor, model


def caption_images(images, processor, model, dev) -> list:
    import torch
    conf = Config()

    messages = [[{"role": "user", "content": [{"type": "image"},
                                              {"type": "text", "text": conf.CAPTION_PROMPT}]}]
                for _ in images]
    prompts = [processor.apply_chat_template(m, add_generation_prompt=True) for m in messages]
    inputs = processor(text=prompts, images=images, return_tensors="pt", padding=True).to(dev)

    with torch.no_grad():
        generated = model.generate(**inputs, max_new_tokens=conf.CAPTION_MAX_TOKENS,
                                   do_sample=False)
    trimmed = [out[len(inp):] for inp, out in zip(inputs["input_ids"], generated)]
    return [t.strip() for t in processor.batch_decode(trimmed, skip_special_tokens=True)]


def caption_pending(conn, collection=None, batch_size=None, limit=None, shard=0, shards=1,
                    scope=None, prefer_device=None) -> CaptionResult:
    conf = Config()
    logger = custom_logger.get_logger("caption")
    batch_size = batch_size or conf.CAPTION_BATCH_SIZE
    params = {"collection": collection, "shard": shard, "shards": shards,
              "scope_all": (scope or conf.CAPTION_SCOPE) == "all"}

    with conn.cursor() as cur:
        cur.execute(COUNT_PENDING, params)
        pending = cur.fetchone()[0]

    if pending == 0:
        logger.info("No keyframes pending captioning.")
        return CaptionResult()

    dev = device.pick_device(prefer_device)
    device.log_device("caption", dev)
    if dev == "cpu":
        logger.warning("Captioning on CPU is impractically slow -- this stage wants a GPU.")
    logger.info(f"Captioning {pending} keyframe(s) with {conf.CAPTION_MODEL}"
                + (f", shard {shard}/{shards}" if shards > 1 else ""))

    processor, model = load_model(dev)
    result = CaptionResult()
    remaining = limit if limit else pending

    while remaining > 0:
        with conn.cursor() as cur:
            cur.execute(SELECT_PENDING, {**params, "limit": min(batch_size, remaining)})
            rows = cur.fetchall()
        if not rows:
            break

        from PIL import Image
        images, ids = [], []
        for keyframe_id, video_id, shot_index, kf_index in rows:
            path = paths.keyframe_path(video_id, shot_index, kf_index)
            try:
                images.append(Image.open(path).convert("RGB"))
                ids.append(keyframe_id)
            except Exception as e:
                logger.warning(f"keyframe {keyframe_id}: cannot read {path} ({e})")
                result.skipped += 1

        if images:
            captions = caption_images(images, processor, model, dev)
            payload = [(kid, text, conf.CAPTION_MODEL)
                       for kid, text in zip(ids, captions) if text]
            if payload:
                with conn.cursor() as cur:
                    execute_values(cur, INSERT_CAPTIONS, payload, page_size=200)
                conn.commit()
                result.captioned += len(payload)

        remaining -= len(rows)

    logger.info(f"Captioned {result.captioned} keyframe(s), skipped {result.skipped}")
    return result
