
"""
Scene ingest: one `scenes` row per scene.

The scenes are the TransNet ones (`<video_id>.mp4.scenes.txt`, `start end` frame pairs), not the
official master shot boundaries. They are longer, they worked well at the uni competition, and V3C
has 1.97M of them against 4.14M master shots.

Two boundary file formats are supported and auto-detected:

  * `<video_id>.mp4.scenes.txt`                -- `start end` frame pairs, whitespace or comma
  * the official V3C master shot boundary TSVs -- frame columns plus HH:MM:SS.mmm timecodes

Where the files and videos come from is pipeline/datasets.py.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import custom_logger
from pipeline.datasets import entry_id, is_video_file
from pipeline.probe import probe_video, ProbeError
from psycopg2.extras import execute_values

from tqdm import tqdm

# HH:MM:SS(.mmm) or MM:SS(.mmm)
TIMECODE_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?([.,]\d{1,6})?$")
INTEGER_RE = re.compile(r"^\d+$")
FLOAT_RE = re.compile(r"^\d+[.,]\d+$")

# Header keywords, checked longest-first so 'startframe' wins over 'start'.
FRAME_START_KEYS = ("startframe", "start_frame", "framestart", "frame_start")
FRAME_END_KEYS = ("endframe", "end_frame", "frameend", "frame_end")

# Fraction of sampled rows that must agree before a column is treated as a given kind.
COLUMN_KIND_THRESHOLD = 0.6

UPSERT_VIDEO = """
    INSERT INTO videos (video_id, collection, fps, duration_ms, width, height, has_audio)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (video_id) DO UPDATE
        SET collection  = EXCLUDED.collection,
            fps         = EXCLUDED.fps,
            duration_ms = EXCLUDED.duration_ms,
            width       = EXCLUDED.width,
            height      = EXCLUDED.height,
            has_audio   = EXCLUDED.has_audio;
"""

INSERT_SCENES = """
    INSERT INTO scenes (video_id, shot_index, start_frame, end_frame, start_ms, end_ms)
    VALUES %s
    ON CONFLICT DO NOTHING
"""

DELETE_SCENES = "DELETE FROM scenes WHERE video_id = %s;"


@dataclass(frozen=True)
class ShotBoundary:
    shot_index: int
    start_frame: int
    end_frame: int
    start_ms: int
    end_ms: int


@dataclass
class IngestResult:
    video_id: str
    scenes_parsed: int
    scenes_inserted: int
    warnings: list


class BoundaryParseError(RuntimeError):
    pass


def timecode_to_ms(value: str) -> int:
    """Converts HH:MM:SS.mmm / MM:SS.mmm to milliseconds."""
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, (minutes, seconds) = "0", parts
    else:
        raise BoundaryParseError(f"Unrecognized timecode: {value!r}")
    return int(round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000))


def _split_line(line: str) -> list:
    """Splits on tab, then comma, then arbitrary whitespace -- covering all observed formats."""
    if "\t" in line:
        return [f.strip() for f in line.split("\t") if f.strip()]
    if "," in line:
        return [f.strip() for f in line.split(",") if f.strip()]
    return line.split()


def _classify(field: str) -> str:
    if INTEGER_RE.match(field):
        return "int"
    if TIMECODE_RE.match(field):
        return "timecode"
    if FLOAT_RE.match(field):
        return "float"
    return "other"


def _resolve_columns(header, rows):
    """
    Decides which columns hold the frame range.

    Returns (start_col, end_col, kind) where kind is 'frame', 'timecode' or 'seconds'.
    A header naming the frame columns wins; otherwise the first two integer columns are the
    frame range, and failing that the first two time columns are converted using the fps.
    """
    if header:
        lowered = [h.lower().replace(" ", "") for h in header]
        start_col = next((i for i, h in enumerate(lowered) if h in FRAME_START_KEYS), None)
        end_col = next((i for i, h in enumerate(lowered) if h in FRAME_END_KEYS), None)
        if start_col is not None and end_col is not None:
            return start_col, end_col, "frame"

    # Classify by content over a sample. Deliberately majority-based rather than unanimous: a
    # single corrupt line in a thousand-line file must not change what the columns *are*
    sample = rows[:200]
    width = min(len(r) for r in sample)
    kinds = []
    for col in range(width):
        counts = {"int": 0, "timecode": 0, "float": 0, "other": 0}
        for row in sample:
            counts[_classify(row[col])] += 1
        total = len(sample)

        if counts["int"] / total >= COLUMN_KIND_THRESHOLD:
            kinds.append("int")
        elif counts["timecode"] / total >= COLUMN_KIND_THRESHOLD:
            kinds.append("timecode")
        elif (counts["float"] + counts["int"]) / total >= COLUMN_KIND_THRESHOLD:
            kinds.append("float")
        else:
            kinds.append("other")

    int_cols = [i for i, k in enumerate(kinds) if k == "int"]
    if len(int_cols) >= 2:
        return int_cols[0], int_cols[1], "frame"

    timecode_cols = [i for i, k in enumerate(kinds) if k == "timecode"]
    if len(timecode_cols) >= 2:
        return timecode_cols[0], timecode_cols[1], "timecode"

    float_cols = [i for i, k in enumerate(kinds) if k == "float"]
    if len(float_cols) >= 2:
        return float_cols[0], float_cols[1], "seconds"

    raise BoundaryParseError(
        f"Could not find a start/end column pair; detected column kinds {kinds}"
    )


def parse_shot_boundaries(text: str, name: str, fps: float) -> tuple:
    """
    Parses the contents of a boundary file into ordered ShotBoundary records. `name` is only used
    in error messages.

    Returns (boundaries, warnings). Warnings cover recoverable oddities -- overlapping shots,
    gaps, zero-length shots -- which are logged but do not stop the ingest, because a handful of
    malformed lines in one file should not block a 28,450-video collection.
    """
    raw_lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in raw_lines if ln and not ln.startswith("#")]

    if not lines:
        raise BoundaryParseError(f"{name} contains no usable lines")

    rows = [_split_line(ln) for ln in lines]
    rows = [r for r in rows if len(r) >= 2]
    if not rows:
        raise BoundaryParseError(f"{name} has no lines with at least two fields")

    # A first row containing a non-numeric field is a header.
    header = None
    if any(_classify(f) == "other" for f in rows[0]):
        header, rows = rows[0], rows[1:]
        if not rows:
            raise BoundaryParseError(f"{name} contains only a header")

    start_col, end_col, kind = _resolve_columns(header, rows)

    warnings = []
    boundaries = []
    previous_end = None

    for line_number, row in enumerate(rows):
        if len(row) <= max(start_col, end_col):
            warnings.append(f"line {line_number + 1}: too few fields, skipped")
            continue

        try:
            if kind == "frame":
                start_frame, end_frame = int(row[start_col]), int(row[end_col])
                start_ms = int(round(start_frame / fps * 1000))
                end_ms = int(round(end_frame / fps * 1000))
            elif kind == "timecode":
                start_ms, end_ms = timecode_to_ms(row[start_col]), timecode_to_ms(row[end_col])
                start_frame = int(round(start_ms / 1000 * fps))
                end_frame = int(round(end_ms / 1000 * fps))
            else:  # seconds
                start_ms = int(round(float(row[start_col].replace(",", ".")) * 1000))
                end_ms = int(round(float(row[end_col].replace(",", ".")) * 1000))
                start_frame = int(round(start_ms / 1000 * fps))
                end_frame = int(round(end_ms / 1000 * fps))
        except (ValueError, BoundaryParseError) as e:
            warnings.append(f"line {line_number + 1}: unparseable ({e}), skipped")
            continue

        if end_frame < start_frame:
            warnings.append(f"line {line_number + 1}: end {end_frame} before start {start_frame}, skipped")
            continue

        if previous_end is not None and start_frame < previous_end:
            warnings.append(f"line {line_number + 1}: overlaps previous shot (ends {previous_end})")

        boundaries.append(ShotBoundary(
            shot_index=len(boundaries),
            start_frame=start_frame,
            end_frame=end_frame,
            start_ms=max(start_ms, 0),
            end_ms=max(end_ms, 0),
        ))
        previous_end = end_frame

    if not boundaries:
        raise BoundaryParseError(f"{name} yielded no valid shots")

    return boundaries, warnings


def ingest_video(conn, video_path, boundary_text, boundary_name, collection, video_id=None,
                 replace=False) -> IngestResult:
    """
    Probes one video and loads its scenes.

    Runs in the caller's transaction so a batch either lands whole or not at all: a video whose
    scenes are half-written is worse than one that was never ingested, because the gap is invisible.
    """
    logger = custom_logger.get_logger("shot_boundaries")
    video_path = Path(video_path)
    video_id = video_id or entry_id(video_path.name)

    metadata = probe_video(video_path, video_id=video_id)
    boundaries, warnings = parse_shot_boundaries(boundary_text, boundary_name, metadata.fps)

    # Boundaries running past the end of the video mean the file and the video disagree -- usually
    # a boundary file matched to the wrong video, which is worth shouting about.
    frame_count = metadata.frame_count_estimate
    overrun = [b for b in boundaries if b.start_frame > frame_count]
    if overrun:
        warnings.append(
            f"{len(overrun)} shot(s) start past the video's {frame_count} frames "
            f"-- boundary file may not belong to this video"
        )

    with conn.cursor() as cur:
        cur.execute(UPSERT_VIDEO, (
            metadata.video_id, collection, metadata.fps, metadata.duration_ms,
            metadata.width, metadata.height, metadata.has_audio,
        ))

        if replace:
            # Cascades to keyframes, so this throws away embeddings for the video too.
            cur.execute(DELETE_SCENES, (metadata.video_id,))

        rows = [
            (metadata.video_id, b.shot_index, b.start_frame, b.end_frame, b.start_ms, b.end_ms)
            for b in boundaries
        ]
        execute_values(cur, INSERT_SCENES, rows, page_size=1000)
        inserted = cur.rowcount

    for warning in warnings:
        logger.warning(f"{video_id}: {warning}")

    logger.debug(f"{video_id}: {len(boundaries)} shots parsed, {inserted} inserted")
    return IngestResult(video_id, len(boundaries), inserted, warnings)


def ingest_collection(conn, dataset, raw_dir=None, replace=False, limit=None, video_ids=None):
    """
    Ingests every video of one collection that has a scene file.

    Videos come from the collection's own directory, plus `raw_dir` for anything fetched; a fetched
    copy wins. They are paired with scene files on the video id, so `00001.mp4` matches
    `00001.mp4.scenes.txt` and `00001.tsv` alike.
    """
    logger = custom_logger.get_logger("shot_boundaries")

    if dataset.scenes is None:
        logger.error(f"{dataset.name}: no scene files found, looked in {', '.join(dataset.scene_candidates)}")
        return [], []

    videos = dataset.video_files()
    if raw_dir and Path(raw_dir).is_dir():
        # work/raw is shared by every collection, so only ids this collection has scenes for
        # are taken from it.
        fetched = {entry_id(p.name): p for p in Path(raw_dir).iterdir() if is_video_file(p)}
        videos.update(fetched)
    scene_ids = dataset.scenes.ids()

    if video_ids is not None:
        # Restricted to the queued batch. Without this, a purge that empties the download
        # directory makes the next run fall back to scanning the whole local dataset and quietly
        # ingest every video in it rather than the handful actually being processed.
        wanted = set(video_ids)
        videos = {v: p for v, p in videos.items() if v in wanted}
        scene_ids &= wanted

    paired = sorted(set(videos) & scene_ids)
    missing_scenes = sorted(set(videos) - scene_ids)
    orphan_scenes = scene_ids - set(videos)
    if missing_scenes:
        logger.warning(f"{dataset.name}: {len(missing_scenes)} video(s) have no scene file, "
                       f"e.g. {missing_scenes[:5]}")
    if orphan_scenes and dataset.has_videos:
        logger.info(f"{dataset.name}: {len(orphan_scenes)} scene file(s) have no video on this "
                    f"machine")
    if limit:
        paired = paired[:limit]

    logger.info(f"Ingesting scenes for {len(paired)} video(s) from {dataset.name} "
                f"({dataset.scenes.path})")
    texts = dataset.scenes.read_many(paired)

    results, failures = [], []
    with tqdm(paired, desc=f"Ingesting scenes ({dataset.name})", unit="video") as progress:
        for video_id in progress:
            try:
                result = ingest_video(
                    conn, videos[video_id], texts[video_id], f"{video_id} scene file",
                    dataset.name, video_id=video_id, replace=replace,
                )
                conn.commit()
                results.append(result)
            except (ProbeError, BoundaryParseError) as e:
                conn.rollback()
                logger.error(f"{video_id}: {e}")
                failures.append((video_id, str(e)))

    total_scenes = sum(r.scenes_inserted for r in results)
    logger.info(
        f"Done: {len(results)} video(s) ingested, {total_scenes} scene(s) inserted, "
        f"{len(failures)} failure(s)"
    )
    return results, failures
