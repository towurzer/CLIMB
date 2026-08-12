"""
Batch lifecycle: enqueue, fetch, purge, and the loop that keeps them moving.

The per-stage modules find their own work by asking what is missing, which makes them resumable
without any bookkeeping. Two things cannot work that way, and those are what ingest_jobs is for:

  * fetch has no output table to check -- it needs a list of what to download
  * purge has to know when a file is safe to delete

Purging is deliberately per-artifact rather than one "all done" gate, because the working set is
what the 1 TB budget is spent on:

    raw video    -> once decode has produced the web copy and candidate frames
    candidates   -> once every scene of that video has at least one keyframe
    audio        -> once the video has transcript rows, or has no audio at all

The raw file is the biggest and can go first, right after decode. Nothing downstream reads it:
selection uses the candidate frames and the web copy, and every model stage reads the persistent
keyframes.
"""

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import custom_logger
from config import Config
from pipeline import paths

STAGES = ["PENDING", "FETCHED", "DECODED", "SELECTED", "EMBEDDED",
          "OCR_DONE", "CAPTIONED", "ASR_DONE", "LOADED", "PURGED", "FAILED"]

UPSERT_JOB = """
    INSERT INTO ingest_jobs (video_id, collection, source_uri)
    VALUES %s
    ON CONFLICT (video_id) DO NOTHING
"""

CLAIM = """
    UPDATE ingest_jobs
    SET stage = %(to_stage)s, host = %(host)s, claimed_at = now(), attempts = attempts + 1
    WHERE video_id IN (SELECT video_id
                       FROM ingest_jobs
                       WHERE stage = %(from_stage)s
                         AND (%(collection)s IS NULL OR collection = %(collection)s)
                       ORDER BY video_id
                       FOR UPDATE SKIP LOCKED
                       LIMIT %(limit)s)
    RETURNING video_id, collection, source_uri;
"""

SET_STAGE = "UPDATE ingest_jobs SET stage = %s WHERE video_id = ANY(%s);"
SET_FAILED = "UPDATE ingest_jobs SET stage = 'FAILED', last_error = %s WHERE video_id = %s;"
STATUS = """
    SELECT collection, stage, count(*)
    FROM ingest_jobs GROUP BY collection, stage ORDER BY collection, stage;
"""

# Every scene of the video has at least one keyframe, so nothing still needs the candidate frames.
CANDIDATES_DONE = """
    SELECT v.video_id
    FROM videos v
    WHERE (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND EXISTS (SELECT 1 FROM scenes s WHERE s.video_id = v.video_id)
      AND NOT EXISTS (SELECT 1
                      FROM scenes s
                      WHERE s.video_id = v.video_id
                        AND NOT EXISTS (SELECT 1 FROM keyframes k WHERE k.scene_id = s.scene_id));
"""

# Transcribed, or never had audio to transcribe.
AUDIO_DONE = """
    SELECT v.video_id
    FROM videos v
    WHERE (%(collection)s IS NULL OR v.collection = %(collection)s)
      AND (NOT v.has_audio
           OR EXISTS (SELECT 1 FROM transcript_segment t WHERE t.video_id = v.video_id));
"""


@dataclass
class PurgeResult:
    raw: int = 0
    candidates: int = 0
    audio: int = 0
    bytes_freed: int = 0


def enqueue(conn, manifest_path, collection):
    """
    Loads a manifest into ingest_jobs.

    Each line is either a bare source URI, or `video_id<TAB>source_uri`. The bare form takes the
    video id from the filename, which is what the V3C layout gives you.
    """
    from psycopg2.extras import execute_values
    logger = custom_logger.get_logger("runner")

    rows = []
    for line in Path(manifest_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            video_id, source = line.split("\t", 1)
        else:
            source = line
            video_id = Path(source).name.split(".")[0]
        rows.append((video_id.strip(), collection, source.strip()))

    if not rows:
        logger.warning(f"{manifest_path} contained no usable entries.")
        return 0

    with conn.cursor() as cur:
        execute_values(cur, UPSERT_JOB, rows, page_size=1000)
        inserted = cur.rowcount
    conn.commit()
    logger.info(f"Enqueued {inserted} new job(s) from {len(rows)} manifest entries ({collection})")
    return inserted


QUEUED_IDS = """
    SELECT video_id FROM ingest_jobs
    WHERE (%s IS NULL OR collection = %s) AND stage <> 'FAILED';
"""


def queued_video_ids(conn, collection=None):
    """
    Video ids in the job queue, or None when the queue is empty.

    None means "no queue, process whatever is on disk", which is how the local-dataset workflow
    behaves. Anything else restricts the stage to the batch actually being worked on.
    """
    with conn.cursor() as cur:
        cur.execute(QUEUED_IDS, (collection, collection))
        ids = [r[0] for r in cur.fetchall()]
    return ids or None


def claim(conn, from_stage, to_stage, limit, collection=None):
    with conn.cursor() as cur:
        cur.execute(CLAIM, {"from_stage": from_stage, "to_stage": to_stage, "limit": limit,
                            "collection": collection, "host": os.uname().nodename})
        rows = cur.fetchall()
    conn.commit()
    return rows


def fetch(conn, limit=None, collection=None) -> int:
    """
    Downloads pending videos into work/raw.

    The transfer itself is a configurable command template rather than a hardcoded tool, because
    how the collection is reachable (rsync, scp, http, a mounted share) is a property of the
    server, not of this pipeline.
    """
    conf = Config()
    logger = custom_logger.get_logger("runner")
    limit = limit or conf.FETCH_BATCH

    jobs = claim(conn, "PENDING", "PENDING", limit, collection)
    if not jobs:
        logger.info("Nothing to fetch.")
        return 0

    fetched = []
    for video_id, _, source_uri in jobs:
        destination = paths.ensure_parent(paths.raw_video_path(video_id, Path(source_uri).suffix or ".mp4"))
        if destination.exists():
            fetched.append(video_id)
            continue

        command = conf.FETCH_COMMAND.format(source=shlex.quote(source_uri),
                                            dest=shlex.quote(str(destination)))
        try:
            subprocess.run(command, shell=True, check=True, capture_output=True)
            fetched.append(video_id)
        except subprocess.CalledProcessError as e:
            error = e.stderr.decode("utf-8", errors="ignore").strip()[-300:]
            logger.error(f"{video_id}: fetch failed ({error})")
            with conn.cursor() as cur:
                cur.execute(SET_FAILED, (error, video_id))
            conn.commit()

    if fetched:
        with conn.cursor() as cur:
            cur.execute(SET_STAGE, ("FETCHED", fetched))
        conn.commit()
    logger.info(f"Fetched {len(fetched)}/{len(jobs)} video(s)")
    return len(fetched)


def _remove(path) -> int:
    """Deletes a file or directory, returning the bytes reclaimed."""
    path = Path(path)
    if not path.exists():
        return 0
    if path.is_dir():
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        shutil.rmtree(path, ignore_errors=True)
        return total
    size = path.stat().st_size
    path.unlink(missing_ok=True)
    return size


def purge(conn, collection=None, keep_raw=False) -> PurgeResult:
    """Deletes transient artifacts whose downstream stage has finished."""
    logger = custom_logger.get_logger("runner")
    result = PurgeResult()

    with conn.cursor() as cur:
        cur.execute(CANDIDATES_DONE, {"collection": collection})
        candidates_done = [r[0] for r in cur.fetchall()]
        cur.execute(AUDIO_DONE, {"collection": collection})
        audio_done = [r[0] for r in cur.fetchall()]

    # Raw video: safe as soon as decode produced its outputs. Verified on disk rather than from a
    # stage flag, so a half-finished decode cannot lose the only copy of the source.
    if not keep_raw:
        for raw in sorted(Path(paths._work_root() / "raw").glob("*")) if (paths._work_root() / "raw").exists() else []:
            video_id = raw.name.split(".")[0]
            if paths.web_video_path(video_id).exists() and paths.candidate_frames(video_id):
                result.bytes_freed += _remove(raw)
                result.raw += 1

    for video_id in candidates_done:
        directory = paths.candidate_dir(video_id)
        if directory.exists():
            result.bytes_freed += _remove(directory)
            result.candidates += 1

    for video_id in audio_done:
        audio = paths.audio_path(video_id)
        if audio.exists():
            result.bytes_freed += _remove(audio)
            result.audio += 1

    logger.info(
        f"Purged {result.raw} raw video(s), {result.candidates} candidate set(s), "
        f"{result.audio} audio track(s) -- {result.bytes_freed / 1024 ** 3:.2f} GB freed"
    )
    return result


def status(conn, collection=None):
    """Prints the job histogram and how much of the pipeline has actually landed."""
    with conn.cursor() as cur:
        cur.execute(STATUS)
        jobs = cur.fetchall()
        cur.execute("""
            SELECT (SELECT count(*) FROM videos),
                   (SELECT count(*) FROM scenes),
                   (SELECT count(*) FROM keyframes),
                   (SELECT count(*) FROM keyframe_embedding),
                   (SELECT count(*) FROM keyframe_text),
                   (SELECT count(*) FROM keyframe_caption),
                   (SELECT count(*) FROM transcript_segment);
        """)
        videos, scenes, keyframes, embeddings, ocr, captions, transcripts = cur.fetchone()

    if jobs:
        print("ingest_jobs:")
        for collection_name, stage, count in jobs:
            print(f"  {collection_name:10s} {stage:10s} {count:>9,}")
    else:
        print("ingest_jobs: empty (stages can still run without it; only fetch and purge need it)")

    print("\ncontent:")
    for label, value in [("videos", videos), ("scenes", scenes), ("keyframes", keyframes),
                         ("embeddings", embeddings), ("ocr rows", ocr), ("captions", captions),
                         ("transcript segments", transcripts)]:
        print(f"  {label:22s} {value:>9,}")

    work = paths._work_root()
    if work.exists():
        transient = sum(f.stat().st_size for f in work.rglob("*") if f.is_file())
        print(f"\n  transient on disk      {transient / 1024 ** 3:>8.2f} GB")
