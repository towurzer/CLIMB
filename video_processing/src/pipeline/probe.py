

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import custom_logger

FFPROBE_TIMEOUT_SECONDS = 120

"""
ffprobe-based video metadata.
"""

@dataclass(frozen=True)
class VideoMetadata:
    video_id: str
    fps: float
    duration_ms: int
    width: int
    height: int
    has_audio: bool
    frame_count: int | None  # container-reported; absent for some streams

    @property
    def frame_count_estimate(self) -> int:
        """Frame count, falling back to duration x fps when the container does not report one."""
        if self.frame_count:
            return self.frame_count
        return int(round(self.duration_ms / 1000.0 * self.fps))


class ProbeError(RuntimeError):
    pass


def _parse_rate(value) -> float | None:
    """Parses ffprobe's 'num/den' rate strings. Returns None for the 0/0 an unknown rate gives."""
    if not value:
        return None
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    if rate <= 0:
        return None
    return float(rate)


def probe_video(video_path, video_id=None) -> VideoMetadata:
    """
    Reads stream metadata for a single video.

    Raises ProbeError rather than returning a default. A video we cannot probe is a video whose
    timestamps we cannot trust, and it should stay in the job queue as FAILED instead of being
    indexed with plausible-looking wrong numbers.
    """
    logger = custom_logger.get_logger("probe")
    path = Path(video_path)
    video_id = video_id or path.stem

    command = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(path),
    ]

    try:
        result = subprocess.run(
            command, check=True, capture_output=True, timeout=FFPROBE_TIMEOUT_SECONDS
        )
    except FileNotFoundError as e:
        raise ProbeError("ffprobe not found on PATH -- install FFmpeg") from e
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"ffprobe timed out on {path}") from e
    except subprocess.CalledProcessError as e:
        raise ProbeError(f"ffprobe failed on {path}: {e.stderr.decode(errors='ignore').strip()}") from e

    try:
        probed = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError(f"ffprobe returned unparseable JSON for {path}") from e

    streams = probed.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        raise ProbeError(f"No video stream in {path}")

    stream = video_streams[0]

    # avg_frame_rate before r_frame_rate: master shot boundaries index decoded frames, and
    # avg_frame_rate (total frames / duration) is what that numbering follows. r_frame_rate is
    # the container's timing base, which on variable-frame-rate files can be a large multiple of
    # the real rate and would stretch every derived timestamp.
    fps = _parse_rate(stream.get("avg_frame_rate"))
    fps_source = "avg_frame_rate"
    if fps is None:
        fps = _parse_rate(stream.get("r_frame_rate"))
        fps_source = "r_frame_rate"
    if fps is None:
        raise ProbeError(f"Could not determine frame rate for {path}")
    if fps_source != "avg_frame_rate":
        logger.warning(f"{video_id}: avg_frame_rate unusable, fell back to r_frame_rate ({fps:.4f})")

    duration_s = stream.get("duration") or probed.get("format", {}).get("duration")
    if duration_s is None:
        raise ProbeError(f"Could not determine duration for {path}")
    duration_ms = int(round(float(duration_s) * 1000))

    width, height = stream.get("width"), stream.get("height")
    if not width or not height:
        raise ProbeError(f"Could not determine dimensions for {path}")

    frame_count = stream.get("nb_frames")
    frame_count = int(frame_count) if frame_count and str(frame_count).isdigit() else None

    metadata = VideoMetadata(
        video_id=video_id,
        fps=fps,
        duration_ms=duration_ms,
        width=int(width),
        height=int(height),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        frame_count=frame_count,
    )

    logger.debug(
        f"{video_id}: {metadata.width}x{metadata.height} @ {metadata.fps:.4f}fps, "
        f"{metadata.duration_ms}ms, audio={metadata.has_audio}, frames={metadata.frame_count}"
    )
    return metadata
