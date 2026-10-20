"""
Whisper transcription of the extracted audio.

Catches hints about spoken content that have no visual signal at all, and is indexed at scene
granularity by overlapping the segment's time range with the scene's which is why scenes carry
start_ms/end_ms rather than only frame numbers.
"""

from collections import defaultdict
from dataclasses import dataclass

import custom_logger
from config import Config
from pipeline import device, paths
from psycopg2.extras import execute_values
from tqdm import tqdm

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
    INSERT INTO transcript_segment (video_id, start_ms, end_ms, text, lang, no_speech_prob,
                                    avg_logprob, compression_ratio, hallucination)
    VALUES %s
"""

SELECT_SEGMENTS = """
    SELECT video_id, segment_id, start_ms, end_ms, text, no_speech_prob, avg_logprob,
           compression_ratio, hallucination
    FROM transcript_segment
    WHERE text <> ''
    ORDER BY video_id, start_ms;
"""

SET_HALLUCINATION = """
    UPDATE transcript_segment AS t
    SET hallucination = v.flag
    FROM (VALUES %s) AS v(segment_id, flag)
    WHERE t.segment_id = v.segment_id;
"""

# Search never sees a flagged segment, and the vector search only through its embedding, so the
# embedding goes with the flag. Unflagged segments are picked up again by embed-text.
DROP_FLAGGED_EMBEDDINGS = """
    DELETE FROM transcript_embedding e
    USING transcript_segment t
    WHERE t.segment_id = e.segment_id
      AND t.hallucination;
"""

MARK_EMPTY = """
    INSERT INTO transcript_segment (video_id, start_ms, end_ms, text, lang)
    VALUES (%s, 0, 0, '', NULL)
"""


@dataclass
class AsrResult:
    videos: int = 0
    segments: int = 0
    hallucinations: int = 0
    silent: int = 0
    skipped: int = 0


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str
    no_speech_prob: float | None = None
    avg_logprob: float | None = None
    compression_ratio: float | None = None


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _is_filler(text: str, phrases) -> bool:
    """A known phrase Whisper makes from noise, or no letter or digit to search for at all."""
    return _normalise(text) in phrases or not any(c.isalnum() for c in text)


def flag_hallucinations(segments) -> list:
    """
    For one video's segments (Segment, any order), whether each is Whisper filling noise or music
    with text rather than speech. Returns the flags in the order given.

    Whisper's "Thank you." over regulator noise is a known phrase with no speech anywhere near it,
    while the same words inside a conversation are real, so a phrase only counts against a segment
    when it is isolated: no other segment that is not a phrase itself within ASR_ISOLATION_MS.
    Confidence values are None for segments transcribed before migration 011; the rules that need
    them skip those.
    """
    conf = Config()
    phrases = conf.asr_hallucination_phrases()
    order = sorted(range(len(segments)), key=lambda i: segments[i].start_ms)
    speech = [segments[i] for i in order if not _is_filler(segments[i].text, phrases)]

    def isolated(seg) -> bool:
        return not any(other is not seg
                       and other.start_ms < seg.end_ms + conf.ASR_ISOLATION_MS
                       and other.end_ms > seg.start_ms - conf.ASR_ISOLATION_MS
                       for other in speech)

    flags = []
    for seg in segments:
        if seg.compression_ratio is not None and \
                seg.compression_ratio > conf.ASR_MAX_COMPRESSION_RATIO:
            flags.append(True)
        elif not any(c.isalnum() for c in seg.text):
            flags.append(True)
        elif _normalise(seg.text) in phrases:
            flags.append(isolated(seg))
        else:
            flags.append(seg.avg_logprob is not None
                         and seg.avg_logprob < conf.ASR_ISOLATED_MIN_AVG_LOGPROB
                         and isolated(seg))
    return flags


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
    """Returns (segments, language), the segments as Segment."""
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
            out.append(Segment(int(s.start * 1000), int(s.end * 1000), text, s.no_speech_prob,
                               s.avg_logprob, s.compression_ratio))
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

    with tqdm(videos, desc="Transcribing videos", unit="video") as progress:
        for video_id in progress:
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
                    rows = [(video_id, s.start_ms, s.end_ms, s.text, language, s.no_speech_prob,
                             s.avg_logprob, s.compression_ratio, flag)
                            for s, flag in zip(segments, flag_hallucinations(segments))]
                    execute_values(cur, INSERT_SEGMENTS, rows, page_size=500)
                    result.segments += len(rows)
                    result.hallucinations += sum(1 for row in rows if row[-1])
                else:
                    # An empty marker row, so a video that genuinely has no speech is not retried on
                    # every run. It contributes nothing to the tsvector index.
                    cur.execute(MARK_EMPTY, (video_id,))
                    result.silent += 1
            conn.commit()
            result.videos += 1

    logger.info(f"Transcribed {result.videos} video(s): {result.segments} segment(s), "
                f"{result.hallucinations} of them taken for hallucinations, "
                f"{result.silent} with no speech, {result.skipped} skipped")
    return result


def reclassify(conn) -> tuple:
    """
    Applies flag_hallucinations to every stored segment again, after the thresholds or the phrase
    list changed. Returns (flagged, unflagged): how many segments changed which way.
    """
    logger = custom_logger.get_logger("asr")
    with conn.cursor() as cur:
        cur.execute(SELECT_SEGMENTS)
        by_video = defaultdict(list)
        for video_id, segment_id, start, end, text, nsp, logprob, ratio, was in cur.fetchall():
            by_video[video_id].append((segment_id, was, Segment(start, end, text, nsp, logprob,
                                                                ratio)))
        changed = []
        for rows in by_video.values():
            flags = flag_hallucinations([segment for _, _, segment in rows])
            changed += [(segment_id, flag) for (segment_id, was, _), flag in zip(rows, flags)
                        if flag != was]
        if changed:
            execute_values(cur, SET_HALLUCINATION, changed, page_size=1000)
        cur.execute(DROP_FLAGGED_EMBEDDINGS)
    conn.commit()
    flagged = sum(1 for _, flag in changed if flag)
    logger.info(f"Reclassified transcripts: {flagged} segment(s) now taken for hallucinations, "
                f"{len(changed) - flagged} no longer"
                + ("; run embed-text to embed those" if len(changed) > flagged else ""))
    return flagged, len(changed) - flagged
