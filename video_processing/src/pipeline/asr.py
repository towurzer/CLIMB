"""
Whisper transcription of the extracted audio.

Catches hints about spoken content that have no visual signal at all, and is indexed at scene
granularity by overlapping the segment's time range with the scene's which is why scenes carry
start_ms/end_ms rather than only frame numbers.
"""

from dataclasses import dataclass

import custom_logger
from config import Config
from pipeline import device, paths
from psycopg2.extras import execute_values

SELECT_PENDING = """
    SELECT v.video_id
    FROM videos v
    WHERE v.has_audio
      AND NOT EXISTS (SELECT 1 FROM transcript_segment t WHERE t.video_id = v.video_id)
      AND (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND mod(abs(hashtext(v.video_id)), %(shards)s) = %(shard)s
    ORDER BY v.video_id
    LIMIT %(limit)s;
"""

INSERT_SEGMENTS = """
    INSERT INTO transcript_segment (video_id, start_ms, end_ms, text, lang)
    VALUES %s
"""

MARK_EMPTY = """
    INSERT INTO transcript_segment (video_id, start_ms, end_ms, text, lang)
    VALUES (%s, 0, 0, '', NULL)
"""


@dataclass
class AsrResult:
    videos: int = 0
    segments: int = 0
    silent: int = 0
    skipped: int = 0


def load_model(dev: str):
    """faster-whisper, loaded lazily. int8 on CPU, float16 on GPU."""
    conf = Config()
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ImportError(
            "faster-whisper is not installed: pip install faster-whisper"
        ) from e
    compute_type = "float16" if dev == "cuda" else "int8"
    return WhisperModel(conf.ASR_MODEL, device=("cuda" if dev == "cuda" else "cpu"),
                        compute_type=compute_type)


def transcribe(model, audio_path):
    """Returns (segments, language). Segments are (start_ms, end_ms, text)."""
    conf = Config()
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=conf.ASR_BEAM_SIZE,
        vad_filter=True,          # skips silence, which is most of a lot of V3C audio
        condition_on_previous_text=False,  # stops one hallucination poisoning the rest
    )
    out = []
    for s in segments:
        text = (s.text or "").strip()
        if text:
            out.append((int(s.start * 1000), int(s.end * 1000), text))
    return out, getattr(info, "language", None)


def asr_pending(conn, collection=None, limit=None, shard=0, shards=1,
                prefer_device=None) -> AsrResult:
    logger = custom_logger.get_logger("asr")
    params = {"collection": collection, "shard": shard, "shards": shards,
              "limit": limit if limit else None}

    with conn.cursor() as cur:
        cur.execute(SELECT_PENDING, params)
        videos = [r[0] for r in cur.fetchall()]

    if not videos:
        logger.info("No videos pending transcription.")
        return AsrResult()

    dev = device.pick_device(prefer_device)
    device.log_device("asr", dev)
    logger.info(f"Transcribing {len(videos)} video(s) with {Config.ASR_MODEL}"
                + (f", shard {shard}/{shards}" if shards > 1 else ""))

    model = load_model(dev)
    result = AsrResult()

    for video_id in videos:
        audio = paths.audio_path(video_id)
        if not audio.exists():
            logger.warning(f"{video_id}: no audio at {audio}")
            result.skipped += 1
            continue
        try:
            segments, language = transcribe(model, audio)
        except Exception as e:
            logger.warning(f"{video_id}: transcription failed ({e})")
            result.skipped += 1
            continue

        with conn.cursor() as cur:
            if segments:
                execute_values(cur, INSERT_SEGMENTS,
                               [(video_id, s, e, t, language) for s, e, t in segments],
                               page_size=500)
                result.segments += len(segments)
            else:
                # An empty marker row, so a video that genuinely has no speech is not retried on
                # every run. It contributes nothing to the tsvector index.
                cur.execute(MARK_EMPTY, (video_id,))
                result.silent += 1
        conn.commit()
        result.videos += 1

    logger.info(f"Transcribed {result.videos} video(s): {result.segments} segment(s), "
                f"{result.silent} with no speech, {result.skipped} skipped")
    return result
