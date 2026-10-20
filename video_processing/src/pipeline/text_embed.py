"""
Semantic embeddings over the transcript segments, the VLM captions and the V3C video info.
"""

from dataclasses import dataclass

import custom_logger
from config import Config
from pipeline import device
from pipeline.video_info import VideoInfo
from psycopg2.extras import execute_values
from tqdm import tqdm

SELECT_MODEL = "SELECT model_id, name, dims FROM embedding_model WHERE name = %s;"

SELECT_PENDING_TRANSCRIPT = """
    SELECT s.segment_id, s.text
    FROM transcript_segment s
             JOIN videos v ON v.video_id = s.video_id
    WHERE s.text <> ''
      AND NOT s.hallucination
      AND NOT EXISTS (SELECT 1
                      FROM transcript_embedding e
                      WHERE e.segment_id = s.segment_id
                        AND e.model_id = %(model_id)s)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(s.segment_id, %(shards)s) = %(shard)s
    ORDER BY s.segment_id
    LIMIT %(limit)s;
"""

INSERT_TRANSCRIPT = """
    INSERT INTO transcript_embedding (segment_id, model_id, embedding)
    VALUES %s ON CONFLICT (segment_id, model_id) DO NOTHING
"""

SELECT_PENDING_CAPTION = """
    SELECT c.keyframe_id, c.caption
    FROM keyframe_caption c
             JOIN keyframes k ON k.keyframe_id = c.keyframe_id
             JOIN videos v ON v.video_id = k.video_id
    WHERE c.caption <> ''
      AND NOT EXISTS (SELECT 1
                      FROM caption_embedding e
                      WHERE e.keyframe_id = c.keyframe_id
                        AND e.model_id = %(model_id)s)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(c.keyframe_id, %(shards)s) = %(shard)s
    ORDER BY c.keyframe_id
    LIMIT %(limit)s;
"""

INSERT_CAPTION = """
    INSERT INTO caption_embedding (keyframe_id, model_id, embedding)
    VALUES %s ON CONFLICT (keyframe_id, model_id) DO NOTHING
"""


# The passage is built in Python (VideoInfo.passage), so the SQL only has to skip rows that would
# produce an empty one. hashtext() because the shard split needs an integer and video ids are text;
# ::bigint because abs() of the one int4 hash -2147483648 overflows.
SELECT_PENDING_INFO = """
    SELECT i.video_id, i.title, i.tags, i.description
    FROM video_info i
             JOIN videos v ON v.video_id = i.video_id
    WHERE (i.title <> '' OR i.description <> '' OR cardinality(i.tags) > 0)
      AND NOT EXISTS (SELECT 1
                      FROM video_info_embedding e
                      WHERE e.video_id = i.video_id
                        AND e.model_id = %(model_id)s)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(abs(hashtext(i.video_id)::bigint), %(shards)s) = %(shard)s
    ORDER BY i.video_id
    LIMIT %(limit)s;
"""

COUNT_PENDING_INFO = """
    SELECT count(*)
    FROM video_info i
             JOIN videos v ON v.video_id = i.video_id
    WHERE (i.title <> '' OR i.description <> '' OR cardinality(i.tags) > 0)
      AND NOT EXISTS (SELECT 1
                      FROM video_info_embedding e
                      WHERE e.video_id = i.video_id
                        AND e.model_id = %(model_id)s)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(abs(hashtext(i.video_id)::bigint), %(shards)s) = %(shard)s;
"""

INSERT_INFO = """
    INSERT INTO video_info_embedding (video_id, model_id, embedding)
    VALUES %s ON CONFLICT (video_id, model_id) DO NOTHING
"""


@dataclass
class TextEmbedResult:
    transcript: int = 0
    caption: int = 0
    info: int = 0


def _info_passage(row) -> tuple:
    video_id, title, tags, description = row
    return video_id, VideoInfo(title=title, tags=list(tags), description=description).passage()


def as_passage(text: str) -> str:
    return f"passage: {text}"


def as_query(text: str) -> str:
    return f"query: {text}"


def load_model(dev: str):
    from transformers import AutoModel, AutoTokenizer

    name = Config.TEXT_MODEL
    tokenizer = AutoTokenizer.from_pretrained(name)
    model = AutoModel.from_pretrained(name, dtype=device.pick_dtype(dev)).to(dev).eval()
    return tokenizer, model


def encode(texts, tokenizer, model, dev):
    """Mean-pools over the attention mask, which is what e5 expects, then L2-normalizes."""
    import torch

    inputs = tokenizer(texts, padding=True, truncation=True,
                       max_length=Config.TEXT_MAX_TOKENS, return_tensors="pt").to(dev)
    with torch.no_grad():
        hidden = model(**inputs).last_hidden_state
        mask = inputs["attention_mask"].unsqueeze(-1).to(hidden.dtype)
        pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
        pooled = pooled / pooled.norm(p=2, dim=-1, keepdim=True)
    return pooled.float().cpu().numpy()


def to_pgvector(values) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values.tolist()) + "]"


def _run(conn, select_sql, insert_sql, model_id, params, tokenizer, model, dev, batch_size,
         label, total, to_text=None):
    logger = custom_logger.get_logger("text_embed")
    done = 0
    with tqdm(total=total, desc=f"Embedding {label}", unit="item") as progress:
        while True:
            with conn.cursor() as cur:
                cur.execute(select_sql, {**params, "model_id": model_id, "limit": batch_size})
                rows = cur.fetchall()
            if not rows:
                break
            if to_text:
                rows = [to_text(row) for row in rows]
            vectors = encode([as_passage(text) for _, text in rows], tokenizer, model, dev)
            payload = [(key, model_id, to_pgvector(v)) for (key, _), v in zip(rows, vectors)]
            with conn.cursor() as cur:
                execute_values(cur, insert_sql, payload, page_size=500)
            conn.commit()
            done += len(payload)
            progress.update(len(payload))
    return done


def embed_text_pending(conn, collection=None, batch_size=None, shard=0, shards=1,
                       prefer_device=None) -> TextEmbedResult:
    conf = Config()
    logger = custom_logger.get_logger("text_embed")
    batch_size = batch_size or conf.TEXT_BATCH_SIZE

    with conn.cursor() as cur:
        cur.execute(SELECT_MODEL, (conf.TEXT_MODEL,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"Text model '{conf.TEXT_MODEL}' is not registered in embedding_model.")
    model_id, name, dims = row

    dev = device.pick_device(prefer_device)
    device.log_device("text_embed", dev)
    logger.info(f"Embedding text with {name} ({dims}d)"
                + (f", shard {shard}/{shards}" if shards > 1 else ""))

    tokenizer, model = load_model(dev)
    params = {"collection": collection, "shard": shard, "shards": shards}
    result = TextEmbedResult()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM transcript_segment s JOIN videos v ON v.video_id = s.video_id "
            "WHERE s.text <> '' AND NOT s.hallucination "
            "AND NOT EXISTS (SELECT 1 FROM transcript_embedding e "
            "WHERE e.segment_id = s.segment_id AND e.model_id = %s) "
            "AND (%s IS NULL OR v.collection = %s) AND mod(s.segment_id, %s) = %s",
            (model_id, collection, collection, shards, shard),
        )
        transcript_total = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM keyframe_caption c JOIN keyframes k ON k.keyframe_id = c.keyframe_id "
            "JOIN videos v ON v.video_id = k.video_id WHERE c.caption <> '' "
            "AND NOT EXISTS (SELECT 1 FROM caption_embedding e "
            "WHERE e.keyframe_id = c.keyframe_id AND e.model_id = %s) "
            "AND (%s IS NULL OR v.collection = %s) AND mod(c.keyframe_id, %s) = %s",
            (model_id, collection, collection, shards, shard),
        )
        caption_total = cur.fetchone()[0]
        cur.execute(COUNT_PENDING_INFO, {**params, "model_id": model_id})
        info_total = cur.fetchone()[0]

    result.transcript = _run(conn, SELECT_PENDING_TRANSCRIPT, INSERT_TRANSCRIPT, model_id,
                             params, tokenizer, model, dev, batch_size, "transcript",
                             transcript_total)
    result.caption = _run(conn, SELECT_PENDING_CAPTION, INSERT_CAPTION, model_id,
                          params, tokenizer, model, dev, batch_size, "caption", caption_total)
    result.info = _run(conn, SELECT_PENDING_INFO, INSERT_INFO, model_id, params, tokenizer,
                       model, dev, batch_size, "video info", info_total, to_text=_info_passage)

    logger.info(f"Embedded {result.transcript} transcript segment(s), "
                f"{result.caption} caption(s) and {result.info} video info(s)")
    return result
