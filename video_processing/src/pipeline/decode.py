"""
Single-pass decode: one ffmpeg invocation per video, two outputs -- candidate frames for keyframe
selection, and audio for ASR.

There is no web copy any more. The sources are already 480x270 H.264, so the frontend plays them
directly (backend/datasets.js finds them) and transcoding them again would only cost time.
"""

import math
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

import custom_logger
from config import Config
from pipeline import paths
from pipeline.datasets import find_source_video
from tqdm import tqdm

# Fraction of the expected candidate frames that must actually be on disk. Container durations are
# not always exact, so this is not 1.0
CANDIDATE_COUNT_TOLERANCE = 0.98


@dataclass
class DecodeResult:
    video_id: str
    ok: bool
    candidate_count: int = 0
    audio: str | None = None
    error: str | None = None
    damaged: bool = False


# If the file is broken we can't do anything about it, if it just failed we can retry
DECODER_ERROR_SIGNATURES = (
    "Invalid NAL unit size",
    "Error splitting the input into NAL units",
    "Error submitting packet to decoder",
    "missing picture in access unit",
    "no frame!",
    "out of range",
    "Invalid data found when processing input",
    "corrupt",
)


def looks_damaged(stderr: str) -> bool:
    return any(signature in stderr for signature in DECODER_ERROR_SIGNATURES)


def candidate_stride(fps: float) -> int:
    """Number of source frames between consecutive candidates."""
    return max(1, int(round(fps / Config.CANDIDATE_FPS)))


def candidate_source_frame(candidate_number: int, fps: float) -> int:
    """
    Source frame number of the Nth candidate, where N is 1-based as ffmpeg numbers its output.
    """
    return (candidate_number - 1) * candidate_stride(fps)


def candidate_timestamp_ms(candidate_number: int, fps: float) -> int:
    """Source timestamp of the Nth candidate, derived from its frame number."""
    return int(round(candidate_source_frame(candidate_number, fps) / fps * 1000))


def expected_candidate_count(duration_ms: int, fps: float) -> int:
    """How many candidates a video of this length should yield."""
    frames = round(duration_ms / 1000 * fps)
    return math.ceil(frames / candidate_stride(fps))


def build_command(video_id, source_path, fps, with_audio=True) -> list:
    """
    Assembles the ffmpeg command.

    Kept as a pure function so the command can be inspected and unit-tested without decoding
    anything
    """
    conf = Config()

    candidate_out = paths.ensure_parent(paths.candidate_pattern(video_id))
    stride = candidate_stride(fps)

    command = [
        "ffmpeg", "-y",
        "-nostdin",          # many of these run at once; none of them may eat the terminal
        "-loglevel", "error",
        "-threads", str(conf.FFMPEG_THREADS),
        "-i", str(source_path),
    ]

    # Output 1: candidate frames for keyframe selection. Selected on the frame counter rather than
    # with the fps filter -- see candidate_source_frame(). min(H,ih) never upscales.
    # fps_mode=passthrough is required: without it ffmpeg re-times the selected frames to a
    # constant rate and duplicates frames to fill the gaps, which would break the exact
    # candidate-to-source-frame correspondence.
    command += [
        "-map", "0:v:0",
        "-vf", f"select='not(mod(n\\,{stride}))',scale=-2:min({conf.CANDIDATE_HEIGHT}\\,ih)",
        "-fps_mode", "passthrough",
        "-q:v", str(conf.CANDIDATE_JPEG_QUALITY),
        str(candidate_out),
    ]

    # Output 2: audio for ASR. Skipped entirely when the probe found no audio stream -- asking
    # ffmpeg to map a stream that does not exist fails the whole command, taking the candidate
    # frames down with it.
    if with_audio:
        audio_out = paths.ensure_parent(paths.audio_path(video_id))
        command += [
            "-map", "0:a:0",
            "-ac", "1", "-ar", str(conf.AUDIO_SAMPLE_RATE),
            "-c:a", "libopus", "-b:a", conf.AUDIO_BITRATE,
            str(audio_out),
        ]

    return command


def _clear_outputs(video_id, with_audio):
    for frame in paths.candidate_frames(video_id):
        frame.unlink()
    if with_audio:
        paths.audio_path(video_id).unlink(missing_ok=True)


def decode_video(video_id, source_path, fps, duration_ms=None, with_audio=True, force=False,
                 known_damaged=False) -> DecodeResult:
    """
    Decodes one video into its two outputs. Idempotent unless `force` is set.

    When `duration_ms` is supplied the candidate frames are counted against what the video's
    length implies, and a short extraction fails the video. ffmpeg does not always tell you: with
    a full disk it happily exits 0 having written a fraction of the frames -- observed here as
    218 candidates where 1632 were expected, across 8 of 200 videos, with no error anywhere.
    Silently thin keyframe coverage is close to undetectable later, so it is caught here instead.
    """
    logger = custom_logger.get_logger("decode")

    existing = len(paths.candidate_frames(video_id))
    audio_ready = not with_audio or paths.audio_path(video_id).exists()
    if not force and existing and audio_ready:
        # "Outputs exist" is not the same as "outputs are complete". A run killed by a full disk
        # leaves exactly this state behind, and accepting it here would make the damage permanent:
        # every subsequent run would skip the video and no stage downstream can tell that half its
        # keyframes were never written. A short count means re-decode, unless we already know the
        # bitstream is damaged and short is the best this video will ever do.
        adequate = (
            not duration_ms
            or known_damaged
            or existing >= expected_candidate_count(duration_ms, fps) * CANDIDATE_COUNT_TOLERANCE
        )
        if adequate:
            logger.debug(f"{video_id}: already decoded ({existing} candidates), skipping")
            return DecodeResult(video_id, True, existing,
                                str(paths.audio_path(video_id)) if with_audio else None,
                                damaged=known_damaged)
        logger.warning(
            f"{video_id}: previous decode left {existing} candidate frames, expected "
            f"{expected_candidate_count(duration_ms, fps)} -- re-decoding"
        )

    # Clear stale outputs first. ffmpeg overwrites by number, so re-decoding a video that now
    # yields fewer frames would silently leave the tail of the previous run behind, and selection
    # would happily pick keyframes from a video that no longer exists in that form.
    _clear_outputs(video_id, with_audio)

    command = build_command(video_id, source_path, fps, with_audio=with_audio)
    try:
        completed = subprocess.run(command, check=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE)
    except FileNotFoundError:
        return DecodeResult(video_id, False, error="ffmpeg not found on PATH")
    except subprocess.CalledProcessError as e:
        # A failed run leaves partial outputs, and partial candidates are what the skip check
        # above would otherwise have to tell apart from finished ones.
        _clear_outputs(video_id, with_audio)
        stderr = e.stderr.decode("utf-8", errors="ignore").strip()
        return DecodeResult(video_id, False, error=stderr[-500:] or "ffmpeg failed")
    # Kept even on success: ffmpeg reports a damaged bitstream on stderr and still exits 0.
    decode_stderr = completed.stderr.decode("utf-8", errors="ignore")

    candidate_count = len(paths.candidate_frames(video_id))
    if candidate_count == 0:
        return DecodeResult(video_id, False, error="ffmpeg produced no candidate frames")

    damaged = False
    if duration_ms:
        expected = expected_candidate_count(duration_ms, fps)
        if candidate_count < expected * CANDIDATE_COUNT_TOLERANCE:
            # A short extraction has two very different causes and they need opposite handling.
            if looks_damaged(decode_stderr):
                # The file is broken and will be just as broken next time, so failing it would
                # only produce a job that retries forever -- and would throw away the part that
                # decoded fine, which for the two known-bad V3C1_200 videos is ~73% of the
                # content. Index what we got and record why it is thin.
                damaged = True
                logger.warning(
                    f"{video_id}: damaged bitstream, indexing partial content "
                    f"({candidate_count}/{expected} candidate frames)"
                )
            else:
                # Clean decode but short output means something outside the file went wrong --
                # a full disk is the one we hit. That is worth retrying, so fail the job.
                return DecodeResult(
                    video_id, False,
                    error=f"truncated extraction with no decoder errors: {candidate_count} "
                          f"candidate frames, expected {expected} for {duration_ms} ms at "
                          f"{fps:.3f} fps -- out of disk space?",
                )

    return DecodeResult(
        video_id, True,
        candidate_count=candidate_count,
        audio=str(paths.audio_path(video_id)) if with_audio else None,
        damaged=damaged,
    )


def _decode_worker(args):
    video_id, source_path, fps, duration_ms, with_audio, force, known_damaged = args
    return decode_video(video_id, source_path, fps, duration_ms=duration_ms,
                        with_audio=with_audio, force=force, known_damaged=known_damaged)


def decode_batch(jobs, force=False, workers=None):
    """
    Decodes a batch of videos in parallel.

    `jobs` is an iterable of (video_id, source_path, fps, duration_ms, has_audio, known_damaged).
    fps, duration_ms and has_audio come from the probe performed during scene ingest, so this
    stage never re-inspects the file. duration_ms is what makes the truncated-extraction check
    possible.
    """
    conf = Config()
    logger = custom_logger.get_logger("decode")

    jobs = list(jobs)
    if not jobs:
        logger.warning("No videos to decode.")
        return []

    workers = workers or conf.DECODE_WORKERS

    logger.info(
        f"Decoding {len(jobs)} video(s) with {workers} worker(s) x {conf.FFMPEG_THREADS} threads"
    )

    payload = [(vid, path, fps, dur, has_audio, force, dmg)
               for vid, path, fps, dur, has_audio, dmg in jobs]
    results = []

    with ProcessPoolExecutor(max_workers=workers,
                             initializer=custom_logger.setup_worker_logging) as executor:
        futures = {executor.submit(_decode_worker, item): item[0] for item in payload}
        with tqdm(total=len(futures), desc="Decoding videos", unit="video") as progress:
            for future in as_completed(futures):
                video_id = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = DecodeResult(video_id, False, error=f"worker crashed: {e}")

                results.append(result)
                if result.ok:
                    logger.debug(f"{video_id}: {result.candidate_count} candidate frames")
                else:
                    logger.error(f"{video_id}: {result.error}")
                progress.update()

    succeeded = sum(1 for r in results if r.ok)
    damaged = sum(1 for r in results if r.damaged)
    total_candidates = sum(r.candidate_count for r in results)
    logger.info(
        f"Decoded {succeeded}/{len(results)} video(s), {total_candidates} candidate frames"
        + (f", {damaged} indexed from a damaged bitstream" if damaged else "")
    )
    return results


SELECT_DECODABLE = """
    SELECT v.video_id, v.collection, v.fps, v.duration_ms, v.has_audio, v.damaged,
           EXISTS (SELECT 1 FROM keyframes k WHERE k.video_id = v.video_id) AS has_keyframes
    FROM videos v
    WHERE (%s IS NULL OR v.collection = %s)
    ORDER BY v.video_id
    LIMIT %s;
"""


def decode_from_database(conn, collection=None, limit=None, force=False, workers=None):
    """
    Decodes videos that scene ingest has already probed.

    Driving this off `videos` rather than a directory listing means the audio flag comes from the
    probe instead of being guessed, and it guarantees we only decode videos whose scenes are
    already loaded -- decoding a video with no scenes produces candidate frames nothing will ever
    select from.
    """
    logger = custom_logger.get_logger("decode")

    with conn.cursor() as cur:
        cur.execute(SELECT_DECODABLE, (collection, collection, limit))
        rows = cur.fetchall()

    jobs, missing, finished = [], [], 0
    for video_id, video_collection, fps, duration_ms, has_audio, damaged, has_keyframes in rows:
        # Selected means decoded: selection consumes the candidates and purge may already have
        # deleted them.
        if has_keyframes and not force:
            finished += 1
            continue
        source = find_source_video(video_id, video_collection)
        if source is None:
            missing.append(video_id)
            continue
        jobs.append((video_id, source, fps, duration_ms, has_audio, damaged))

    if finished:
        logger.info(f"{finished} video(s) already decoded and selected, skipping")
    if missing:
        logger.warning(f"{len(missing)} video(s) still need decoding but have no source file, "
                       f"e.g. {missing[:5]}")

    results = decode_batch(jobs, force=force, workers=workers)

    damaged_ids = [r.video_id for r in results if r.damaged]
    if damaged_ids:
        with conn.cursor() as cur:
            cur.execute("UPDATE videos SET damaged = TRUE WHERE video_id = ANY(%s);", (damaged_ids,))
        conn.commit()
        logger.warning(f"Flagged {len(damaged_ids)} video(s) as damaged: {damaged_ids}")

    return results
