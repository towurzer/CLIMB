"""
Where the source collections live, and how to read the metadata shipped with them.

The layout on the HDD is:

    $CLIMB_SOURCE_ROOT/
        VBS/videos/v3c/00001.mp4
        VBS/videos/mvk/Ambon_Apr2012_0001.mp4
        VBS/videos/lhe/LHE01.mp4
        V3Cmeta/scenesV3C/scenes/00001.mp4.scenes.txt    or  V3Cmeta/scenesV3C.tgz
        V3Cmeta/info/00001.json                          or  V3Cmeta/infoV3C.tgz
        MVKmeta/scenes/Ambon_Apr2012_0001.mp4.scenes.txt or  MVKmeta/scenesMVK12.tgz
        LHEmeta/scenes/LHE01.mp4.scenes.txt              or  LHEmeta/scenesLHE.tgz

The relative paths are in /datasets.json at the repo root rather than in here, because the backend
has to find the very same video files to play them, and two copies of a layout drift.

Metadata entries list an extracted directory first and the archive second, and the first one that
exists wins. The archive is read directly, so a machine that only has the .tgz needs no unpacking.

MVKmeta also contains `mvk12scenes.tgz`. Do not use it: its file names do not match the videos
(`BigIsland1_Jan2023_0009` where the video is `BigIsland_Jan2023_0009`). `scenesMVK12.tgz` does.

Any path can be overridden per collection with CLIMB_<NAME>_VIDEOS / _SCENES / _INFO. An absolute
value is used as-is, a relative one is taken relative to CLIMB_SOURCE_ROOT.
"""

import json
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path

from config import Config, PROJECT_ROOT
from pipeline import paths

REGISTRY_PATH = PROJECT_ROOT / "datasets.json"

SCENE_SUFFIXES = (".txt", ".tsv", ".csv")
INFO_SUFFIXES = (".json",)


def is_video_file(path) -> bool:
    """
    A video, by extension. Hidden files are not: the HDD has macOS `._00044.mp4` resource forks
    next to some videos, 4 KB each, with an id of ''.
    """
    path = Path(path)
    return (path.is_file() and not path.name.startswith(".")
            and path.suffix.lower() in Config.VIDEO_EXTENSIONS)


def entry_id(name: str) -> str:
    """
    The video id a file belongs to: its name up to the first dot.

    Covers every naming scheme seen so far -- `00001.mp4.scenes.txt`, `00001.json`,
    `10536.info.json`, `Ambon_Apr2012_0001.mp4` -- MVK ids contain underscores but never dots.
    """
    return Path(name).name.split(".")[0]


class MetaSource:
    """
    A collection of per-video text files, read from a directory or straight out of a .tgz.

    Both forms answer the same three questions, so nothing downstream knows or cares which one the
    machine happens to have.
    """

    def __init__(self, path, suffixes):
        self.path = Path(path)
        self.suffixes = suffixes
        self.is_archive = self.path.is_file()

    def __repr__(self):
        return f"MetaSource({self.path})"

    def _wanted(self, name: str) -> bool:
        return name.endswith(self.suffixes) and not Path(name).name.startswith(".")

    def _members(self, archive):
        for member in archive:
            if member.isfile() and self._wanted(member.name):
                yield member

    def ids(self) -> set:
        if self.is_archive:
            with tarfile.open(self.path, "r:*") as archive:
                return {entry_id(m.name) for m in self._members(archive)}
        return {entry_id(p.name) for p in self.path.iterdir()
                if p.is_file() and self._wanted(p.name)}

    def items(self, wanted=None):
        """
        Yields (video_id, text), optionally only for the ids in `wanted`.

        Streams in both forms. The V3C info archive is 80 MB compressed, so building a dict of all
        of it just to look up a few hundred videos would be silly.
        """
        wanted = set(wanted) if wanted is not None else None
        if self.is_archive:
            with tarfile.open(self.path, "r:*") as archive:
                for member in self._members(archive):
                    video_id = entry_id(member.name)
                    if wanted is None or video_id in wanted:
                        yield video_id, archive.extractfile(member).read().decode(
                            "utf-8", errors="replace")
            return
        for path in sorted(self.path.iterdir()):
            if not (path.is_file() and self._wanted(path.name)):
                continue
            video_id = entry_id(path.name)
            if wanted is None or video_id in wanted:
                yield video_id, path.read_text(encoding="utf-8", errors="replace")

    def read_many(self, video_ids) -> dict:
        return dict(self.items(wanted=video_ids))


@dataclass(frozen=True)
class Dataset:
    name: str                       # what videos.collection holds, and what the UI filters on
    videos: Path | None             # None when it is not configured at all
    scenes: MetaSource | None       # None when no candidate exists on this machine
    info: MetaSource | None
    scene_candidates: tuple         # what was looked for, so an error can say where
    info_candidates: tuple

    @property
    def has_videos(self) -> bool:
        return self.videos is not None and self.videos.is_dir()

    def video_files(self) -> dict:
        """video_id -> path for every video file in the collection's directory."""
        if not self.has_videos:
            return {}
        return {entry_id(p.name): p for p in self.videos.iterdir() if is_video_file(p)}

    def video_path(self, video_id: str) -> Path | None:
        if not self.has_videos:
            return None
        return next((p for p in (self.videos / f"{video_id}{ext}" for ext in Config.VIDEO_EXTENSIONS)
                     if p.exists()), None)


def _load_registry() -> dict:
    with open(REGISTRY_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _resolve(value) -> Path | None:
    """Absolute paths as-is; relative ones under CLIMB_SOURCE_ROOT, or nothing if it is unset."""
    path = Path(os.path.expanduser(str(value)))
    if path.is_absolute():
        return path
    root = Config.SOURCE_ROOT
    return Path(os.path.expanduser(root)) / path if root else None


def _candidates(name, key, entry) -> tuple:
    override = os.getenv(f"CLIMB_{name}_{key.upper()}")
    if override:
        return (override,)
    value = entry.get(key) or ()
    return (value,) if isinstance(value, str) else tuple(value)


def _first_existing(candidates, suffixes):
    for candidate in candidates:
        path = _resolve(candidate)
        if path is not None and path.exists():
            return MetaSource(path, suffixes)
    return None


def load(name: str) -> Dataset:
    registry = _load_registry()
    key = name.upper()
    if key not in registry:
        raise KeyError(f"unknown collection {name!r}; datasets.json knows {', '.join(registry)}")
    entry = registry[key]

    videos = _candidates(key, "videos", entry)
    scenes = _candidates(key, "scenes", entry)
    info = _candidates(key, "info", entry)
    return Dataset(
        name=key,
        videos=_resolve(videos[0]) if videos else None,
        scenes=_first_existing(scenes, SCENE_SUFFIXES),
        info=_first_existing(info, INFO_SUFFIXES),
        scene_candidates=tuple(str(_resolve(c) or c) for c in scenes),
        info_candidates=tuple(str(_resolve(c) or c) for c in info),
    )


def all_datasets() -> list:
    return [load(name) for name in _load_registry()]


def selected(collection=None) -> list:
    """
    The collections a stage should work on.

    Naming one asks for exactly that one. Naming none means every collection this machine can
    actually ingest, which needs a scene source -- the videos can still arrive through fetch.
    """
    if collection:
        return [load(collection)]
    return [d for d in all_datasets() if d.scenes is not None]


def find_source_video(video_id: str, collection: str | None) -> Path | None:
    """
    The file to decode a video from: a fetched copy in work/raw first, the collection's own
    directory second. The second is the normal case now that the sources sit on a mounted drive.
    """
    fetched = next((p for p in (paths.raw_video_path(video_id, ext) for ext in Config.VIDEO_EXTENSIONS)
                    if p.exists()), None)
    if fetched is not None:
        return fetched
    if collection:
        try:
            return load(collection).video_path(video_id)
        except KeyError:
            return None
    return None
