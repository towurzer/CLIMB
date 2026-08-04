"""
Single-pass decode: one ffmpeg invocation per video, three outputs.
"""

import math
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import custom_logger
from config import Config
from pipeline import paths

# Fraction of the expected candidate frames that must actually be on disk. Container durations are
# not always exact, so this is not 1.0
CANDIDATE_COUNT_TOLERANCE = 0.98


@dataclass
class DecodeResult:
    video_id: str
    ok: bool
    web_video: str | None = None
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


_nvenc_available = None


def has_nvenc() -> bool:
    """
    Checks whether NVENC can actually encode, by encoding something..
    """
    global _nvenc_available
    if _nvenc_available is not None:
        return _nvenc_available

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
             "-c:v", "h264_nvenc", "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, timeout=60,
        )
        _nvenc_available = result.returncode == 0
    except Exception:
        _nvenc_available = False

    return _nvenc_available


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


def build_command(video_id, source_path, fps, use_gpu=False, with_audio=True) -> list:
    """
    Assembles the three-output ffmpeg command.

    Kept as a pure function so the command can be inspected and unit-tested without decoding
    anything
    """
    conf = Config()

    web_out = paths.ensure_parent(paths.web_video_path(video_id))
    candidate_out = paths.ensure_parent(paths.candidate_pattern(video_id))
    stride = candidate_stride(fps)

    # min(H,ih) never upscales. The course's V3C1_200 videos are already 480x270, and blowing them
    # up to 360 lines would cost bitrate for no additional detail.
    web_scale = f"scale=-2:min({conf.WEB_VIDEO_HEIGHT}\\,ih)"
    candidate_scale = f"scale=-2:min({conf.CANDIDATE_HEIGHT}\\,ih)"
    # select on the frame counter, not the fps filter -- see candidate_source_frame().
    filtergraph = (
        f"[0:v]split=2[web][cand];"
        f"[web]{web_scale}[w];"
        f"[cand]select='not(mod(n\\,{stride}))',{candidate_scale}[c]"
    )

    command = [
        "ffmpeg", "-y",
        "-nostdin",          # many of these run at once; none of them may eat the terminal
        "-loglevel", "error",
        "-threads", str(conf.FFMPEG_THREADS),
        "-i", str(source_path),
        "-filter_complex", filtergraph,
    ]

    # Output 1: web-playable video. -threads again here because the value before -i sets decoder
    # threads only; without this x264 would default to "use every core" in each of N workers.
    command += ["-map", "[w]", "-threads", str(conf.FFMPEG_THREADS)]
    if use_gpu:
        command += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(conf.WEB_VIDEO_CRF)]
    else:
        command += ["-c:v", "libx264", "-preset", conf.WEB_VIDEO_PRESET, "-crf", str(conf.WEB_VIDEO_CRF)]
    if with_audio:
        command += ["-map", "0:a:0", "-c:a", "aac", "-b:a", conf.WEB_AUDIO_BITRATE]
    command += ["-movflags", "+faststart", str(web_out)]

    # Output 2: candidate frames for keyframe selection. fps_mode=passthrough is required --
    # without it ffmpeg re-times the selected frames to a constant rate and duplicates frames to
    # fill the gaps, which would break the exact candidate-to-source-frame correspondence.
    command += [
        "-map", "[c]",
        "-fps_mode", "passthrough",
        "-q:v", str(conf.CANDIDATE_JPEG_QUALITY),
        str(candidate_out),
    ]

    # Output 3: audio for ASR. Skipped entirely when the probe found no audio stream -- asking
    # ffmpeg to map a stream that does not exist fails the whole command, taking the other two
    # outputs down with it.
    if with_audio:
        audio_out = paths.ensure_parent(paths.audio_path(video_id))
        command += [
            "-map", "0:a:0",
            "-ac", "1", "-ar", str(conf.AUDIO_SAMPLE_RATE),
            "-c:a", "libopus", "-b:a", conf.AUDIO_BITRATE,
            str(audio_out),
        ]

    return command


def decode_video(video_id, source_path, fps, duration_ms=None, use_gpu=False, with_audio=True,
                 force=False, known_damaged=False) -> DecodeResult:
    """
    Decodes one video into its three outputs. Idempotent unless `force` is set.

    When `duration_ms` is supplied the candidate frames are counted against what the video's
    length implies, and a short extraction fails the video. ffmpeg does not always tell you: with
    a full disk it happily exits 0 having written a fraction of the frames -- observed here as
    218 candidates where 1632 were expected, across 8 of 200 videos, with no error anywhere.
    Silently thin keyframe coverage is close to undetectable later, so it is caught here instead.
    """
    logger = custom_logger.get_logger("decode")

    web_out = paths.web_video_path(video_id)
    candidate_directory = paths.candidate_dir(video_id)

    if not force and web_out.exists() and any(paths.candidate_frames(video_id)):
        existing = len(list(paths.candidate_frames(video_id)))
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
            return DecodeResult(video_id, True, str(web_out), existing,
                                str(paths.audio_path(video_id)) if with_audio else None,
                                damaged=known_damaged)
        logger.warning(
            f"{video_id}: previous decode left {existing} candidate frames, expected "
            f"{expected_candidate_count(duration_ms, fps)} -- re-decoding"
        )

    # Clear stale candidates first. ffmpeg overwrites by number, so re-decoding a video that now
    # yields fewer frames would silently leave the tail of the previous run behind, and selection
    # would happily pick keyframes from a video that no longer exists in that form.
    if candidate_directory.exists():
        for stale in paths.candidate_frames(video_id):
            stale.unlink()

    # Consumer NVIDIA cards cap concurrent NVENC sessions (historically 2-3, more on newer
    # drivers), and we run DECODE_WORKERS encodes at once. Rather than capping the worker count to
    # the least capable card, let a GPU encode fail and fall back to libx264 for that one video.
    attempts = [True, False] if use_gpu else [False]

    decode_stderr = ""
    for attempt_index, gpu in enumerate(attempts):
        command = build_command(video_id, source_path, fps, use_gpu=gpu, with_audio=with_audio)
        try:
            completed = subprocess.run(command, check=True, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE)
            # Kept even on success: ffmpeg reports a damaged bitstream on stderr and still exits 0.
            decode_stderr = completed.stderr.decode("utf-8", errors="ignore")
            break
        except FileNotFoundError:
            return DecodeResult(video_id, False, error="ffmpeg not found on PATH")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="ignore").strip()
            # Clean up a partial transcode so a resumed run does not mistake it for a finished one.
            web_out.unlink(missing_ok=True)
            if attempt_index < len(attempts) - 1:
                logger.warning(f"{video_id}: GPU encode failed, retrying on CPU ({stderr[-160:]})")
                continue
            return DecodeResult(video_id, False, error=stderr[-500:] or "ffmpeg failed")

    candidate_count = len(list(paths.candidate_frames(video_id)))
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
        web_video=str(web_out),
        candidate_count=candidate_count,
        audio=str(paths.audio_path(video_id)) if with_audio else None,
        damaged=damaged,
    )


def _decode_worker(args):
    video_id, source_path, fps, duration_ms, use_gpu, with_audio, force, known_damaged = args
    return decode_video(video_id, source_path, fps, duration_ms=duration_ms, use_gpu=use_gpu,
                        with_audio=with_audio, force=force, known_damaged=known_damaged)


def decode_batch(jobs, use_gpu=None, force=False, workers=None):
    """
    Decodes a batch of videos in parallel.

    `jobs` is an iterable of (video_id, source_path, fps, duration_ms, has_audio, known_damaged),
    the first five of which
    come from the probe performed during shot boundary ingest, so this stage never re-inspects the
    file. duration_ms is what makes the truncated-extraction check possible.
    """
    conf = Config()
    logger = custom_logger.get_logger("decode")

    jobs = list(jobs)
    if not jobs:
        logger.warning("No videos to decode.")
        return []

    if use_gpu is None:
        use_gpu = has_nvenc()
    workers = workers or conf.DECODE_WORKERS

    logger.info(
        f"Decoding {len(jobs)} video(s) with {workers} worker(s) x {conf.FFMPEG_THREADS} threads, "
        f"encoder={'h264_nvenc' if use_gpu else 'libx264'}"
    )

    payload = [(vid, path, fps, dur, use_gpu, has_audio, force, dmg)
               for vid, path, fps, dur, has_audio, dmg in jobs]
    results = []

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_decode_worker, item): item[0] for item in payload}
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

    succeeded = sum(1 for r in results if r.ok)
    damaged = sum(1 for r in results if r.damaged)
    total_candidates = sum(r.candidate_count for r in results)
    logger.info(
        f"Decoded {succeeded}/{len(results)} video(s), {total_candidates} candidate frames"
        + (f", {damaged} indexed from a damaged bitstream" if damaged else "")
    )
    return results


SELECT_DECODABLE = """
    SELECT v.video_id, v.fps, v.duration_ms, v.has_audio, v.damaged
    FROM videos v
    WHERE (%s IS NULL OR v.collection = %s)
    ORDER BY v.video_id
    LIMIT %s;
"""


def decode_from_database(conn, source_dir, collection=None, limit=None, force=False, workers=None):
    """
    Decodes videos that shot boundary ingest has already probed.

    Driving this off `videos` rather than a directory listing means the audio flag comes from the
    probe instead of being guessed, and it guarantees we only decode videos whose master shots are
    already loaded -- decoding a video with no scenes produces candidate frames nothing will ever
    select from.
    """
    logger = custom_logger.get_logger("decode")
    source_dir = Path(source_dir)

    with conn.cursor() as cur:
        cur.execute(SELECT_DECODABLE, (collection, collection, limit))
        rows = cur.fetchall()

    jobs, missing = [], []
    for video_id, fps, duration_ms, has_audio, damaged in rows:
        source = next(
            (p for p in (source_dir / f"{video_id}{ext}" for ext in Config.VIDEO_EXTENSIONS) if p.exists()),
            None,
        )
        if source is None:
            missing.append(video_id)
            continue
        jobs.append((video_id, source, fps, duration_ms, has_audio, damaged))

    if missing:
        logger.warning(f"{len(missing)} video(s) in the database have no source file, e.g. {missing[:5]}")

    results = decode_batch(jobs, force=force, workers=workers)

    damaged_ids = [r.video_id for r in results if r.damaged]
    if damaged_ids:
        with conn.cursor() as cur:
            cur.execute("UPDATE videos SET damaged = TRUE WHERE video_id = ANY(%s);", (damaged_ids,))
        conn.commit()
        logger.warning(f"Flagged {len(damaged_ids)} video(s) as damaged: {damaged_ids}")

    return results
