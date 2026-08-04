"""
On-disk layout for everything the pipeline produces.

Every path is *derived* from (video_id, shot_index, kf_index) rather than stored in the database.
The old schema kept an absolute `image_path` on all 99,661 keyframe rows and then took it apart
again with `image_path.split('/').pop()` in four places in the backend, plus a
`LIKE '%_kf_00010.jpg'` pattern match to guess a video's thumbnail. At 12.4M keyframes that is
roughly a gigabyte of redundant strings and four places to get wrong. Deriving costs nothing and
cannot drift from the data.

Paths are sharded on the first two characters of the video id -- V3C runs 00001..28450, so that is
~29 directories of ~1,000 videos each, instead of 12.4M thumbnails in one directory where every
readdir() becomes a small tragedy.

    media/                      persistent, survives the raw video being deleted
        video/00/00001.mp4          360p web-playable transcode
        kf/00/00001/00042_1.webp    384px keyframe (detail panel + model input)
        thumbs/00/00001/00042_1.webp 160px keyframe (result grid)
    work/                       transient, deleted by the purge stage
        raw/00001.mp4               downloaded source
        cand/00001/000123.jpg       candidate frames, before selection
        audio/00001.opus            16 kHz mono audio for ASR

NOTE: the backend derives the same paths in JavaScript. If the scheme here changes, the helper in
backend/ has to change with it 
"""

from pathlib import Path

from config import Config

SHARD_WIDTH = 2


def shard_of(video_id: str) -> str:
    """Directory shard for a video id. Short ids are padded so they never collide with ''."""
    return str(video_id)[:SHARD_WIDTH].rjust(SHARD_WIDTH, "0")


def _media_root() -> Path:
    return Path(Config.MEDIA_DIR)


def _work_root() -> Path:
    return Path(Config.WORK_DIR)


# --- persistent -------------------------------------------------------------

def web_video_path(video_id: str) -> Path:
    return _media_root() / "video" / shard_of(video_id) / f"{video_id}.mp4"


def keyframe_dir(video_id: str) -> Path:
    return _media_root() / "kf" / shard_of(video_id) / str(video_id)


def thumbnail_dir(video_id: str) -> Path:
    return _media_root() / "thumbs" / shard_of(video_id) / str(video_id)


def keyframe_name(shot_index: int, kf_index: int) -> str:
    return f"{shot_index:05d}_{kf_index}.webp"


def keyframe_path(video_id: str, shot_index: int, kf_index: int) -> Path:
    return keyframe_dir(video_id) / keyframe_name(shot_index, kf_index)


def thumbnail_path(video_id: str, shot_index: int, kf_index: int) -> Path:
    return thumbnail_dir(video_id) / keyframe_name(shot_index, kf_index)


# --- transient --------------------------------------------------------------

def raw_video_path(video_id: str, suffix: str = ".mp4") -> Path:
    return _work_root() / "raw" / f"{video_id}{suffix}"


def candidate_dir(video_id: str) -> Path:
    return _work_root() / "cand" / str(video_id)


CANDIDATE_SUFFIX = ".jpg"


def candidate_pattern(video_id: str) -> Path:
    """ffmpeg image2 output pattern. Frames are numbered from 1."""
    return candidate_dir(video_id) / f"%06d{CANDIDATE_SUFFIX}"


def candidate_frames(video_id: str) -> list:
    """Existing candidate frames for a video, in ffmpeg's numbering order."""
    return sorted(candidate_dir(video_id).glob(f"*{CANDIDATE_SUFFIX}"))


def audio_path(video_id: str) -> Path:
    return _work_root() / "audio" / f"{video_id}.opus"


def ensure_parent(path) -> Path:
    """Creates a path's parent directory. Returns the path so it can be used inline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
