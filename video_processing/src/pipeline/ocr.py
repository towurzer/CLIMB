"""
OCR over keyframes.

V3C is full of readable text  and KIS-Textual hints mention it constantly. Text search is exact where embeddings are vague, so this
resolves hints SigLIP2 cannot express at all.

Scope is Config.OCR_SCOPE. 'all' (default) reads every keyframe; 'shot' only the first of each

The engine is pluggable (Config.OCR_BACKEND). Both backends run the same PP-OCR models; paddle is
the better of the two, but it needs a `paddlepaddle` wheel that does
not exist for every interpreter, so rapidocr-onnxruntime is the portable fallback.
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
      AND NOT EXISTS (SELECT 1 FROM keyframe_text t WHERE t.keyframe_id = k.keyframe_id)
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
      AND NOT EXISTS (SELECT 1 FROM keyframe_text t WHERE t.keyframe_id = k.keyframe_id)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(k.keyframe_id, %(shards)s) = %(shard)s;
"""

INSERT_TEXT = """
    INSERT INTO keyframe_text (keyframe_id, ocr_text)
    VALUES %s
    ON CONFLICT (keyframe_id) DO NOTHING
"""


@dataclass
class OcrResult:
    processed: int = 0
    with_text: int = 0
    skipped: int = 0


class PaddleReader:
    """
    PaddleOCR. The better engine

    Needs `paddlepaddle`, which ships no wheel for every interpreter -- Python 3.14 has none at
    all, which is why the backend below exists.
    """

    def __init__(self, dev: str):
        from paddleocr import PaddleOCR
        self._engine = PaddleOCR(use_angle_cls=True, lang="en",
                                 use_gpu=(dev == "cuda"), show_log=False)

    def read(self, image_path) -> list:
        result = self._engine.ocr(str(image_path), cls=True)
        lines = []
        for page in result or []:
            for line in page or []:
                # PaddleOCR returns [box, (text, confidence)]
                if len(line) >= 2 and isinstance(line[1], (list, tuple)) and line[1]:
                    lines.append((line[1][0], line[1][1]))
        return lines


class RapidReader:
    """
    rapidocr-onnxruntime: the same PP-OCR models exported to ONNX, CPU-only, no paddle runtime.

    Its models ride along inside the wheel, so unlike paddle there is nothing to download and
    nothing to go stale. Measured here at ~1.6 s/frame.
    """

    def __init__(self, dev: str):
        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR()

    def read(self, image_path) -> list:
        result, _elapsed = self._engine(str(image_path))
        # A frame with no text returns None rather than an empty list.
        # Confidence arrives as a string, so it must be coerced before it is compared to a float.
        return [(line[1], float(line[2])) for line in result or [] if len(line) >= 3]


BACKENDS = {"paddle": PaddleReader, "rapidocr": RapidReader}


def load_reader(dev: str):
    """
    An OCR reader exposing `.read(path) -> [(text, confidence)]`, whichever engine backs it.

    Set CLIMB_OCR_BACKEND to pin one. The default tries paddle and falls back.
    """
    logger = custom_logger.get_logger("ocr")
    requested = (Config.OCR_BACKEND or "auto").lower()

    if requested != "auto":
        if requested not in BACKENDS:
            raise ValueError(f"unknown CLIMB_OCR_BACKEND {requested!r}; "
                             f"expected one of {', '.join(BACKENDS)} or 'auto'")
        logger.info(f"OCR backend: {requested} (pinned)")
        return BACKENDS[requested](dev)

    for name, backend in BACKENDS.items():
        try:
            reader = backend(dev)
        except ImportError:
            continue
        logger.info(f"OCR backend: {name}")
        return reader

    raise ImportError(
        "No OCR backend available. Install one of:\n"
        "  pip install rapidocr-onnxruntime      # CPU, self-contained, works everywhere\n"
        "  pip install paddleocr paddlepaddle-gpu # better, needs a paddle wheel for this Python"
    )


def extract_text(reader, image_path) -> str:
    """Returns recognised text joined by spaces, or '' when the frame has none."""
    pieces = [str(text).strip() for text, confidence in reader.read(image_path)
              if text and confidence >= Config.OCR_MIN_CONFIDENCE]
    return " ".join(p for p in pieces if p)


def ocr_pending(conn, collection=None, batch_size=None, limit=None, shard=0, shards=1,
                scope=None, prefer_device=None) -> OcrResult:
    conf = Config()
    logger = custom_logger.get_logger("ocr")
    batch_size = batch_size or conf.OCR_BATCH_SIZE
    params = {"collection": collection, "shard": shard, "shards": shards,
              "scope_all": (scope or conf.OCR_SCOPE) == "all"}

    with conn.cursor() as cur:
        cur.execute(COUNT_PENDING, params)
        pending = cur.fetchone()[0]

    if pending == 0:
        logger.info("No keyframes pending OCR.")
        return OcrResult()

    dev = device.pick_device(prefer_device)
    device.log_device("ocr", dev)
    logger.info(f"Running OCR on {pending} keyframe(s)"
                + (f", shard {shard}/{shards}" if shards > 1 else ""))

    reader = load_reader(dev)
    result = OcrResult()
    remaining = limit if limit else pending

    while remaining > 0:
        with conn.cursor() as cur:
            cur.execute(SELECT_PENDING, {**params, "limit": min(batch_size, remaining)})
            rows = cur.fetchall()
        if not rows:
            break

        payload = []
        for keyframe_id, video_id, shot_index, kf_index in rows:
            path = paths.keyframe_path(video_id, shot_index, kf_index)
            if not path.exists():
                logger.warning(f"keyframe {keyframe_id}: missing {path}")
                result.skipped += 1
                continue
            try:
                text = extract_text(reader, path)
            except Exception as e:
                logger.warning(f"keyframe {keyframe_id}: OCR failed ({e})")
                result.skipped += 1
                continue
            # Empty results are stored too. Without a row the keyframe stays "pending" forever
            # and every run would re-OCR the same textless frames.
            payload.append((keyframe_id, text))
            result.processed += 1
            if text:
                result.with_text += 1

        if payload:
            with conn.cursor() as cur:
                execute_values(cur, INSERT_TEXT, payload, page_size=500)
            conn.commit()

        remaining -= len(rows)

    logger.info(f"OCR done: {result.processed} processed, {result.with_text} contained text, "
                f"{result.skipped} skipped")
    return result
