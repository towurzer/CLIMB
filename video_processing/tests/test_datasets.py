"""
Finding the source collections, and the scene files that come with them.

All of it runs on a fake layout in tmp_path. Getting this wrong does not crash, it pairs a video
with nothing or with the wrong scene file, so it is pinned here rather than discovered on someone
else's server.
"""

import io
import tarfile

import pytest

from config import Config
from pipeline import datasets
from pipeline.decode import build_command
from pipeline.shot_boundaries import parse_shot_boundaries

SCENES = "0 152\n153 199\n200 249\n"


@pytest.fixture
def source_root(tmp_path, monkeypatch):
    """The HDD layout, with V3C scenes extracted and MVK scenes only as an archive."""
    root = tmp_path / "source"
    (root / "VBS/videos/v3c").mkdir(parents=True)
    (root / "VBS/videos/mvk").mkdir(parents=True)
    for name in ("00001.mp4", "00002.mp4"):
        (root / "VBS/videos/v3c" / name).write_bytes(b"")
    for name in ("Ambon_Apr2012_0001.mp4", "Ambon_Apr2012_0002.mp4", "getfps.sh",
                 "._Ambon_Apr2012_0001.mp4"):
        (root / "VBS/videos/mvk" / name).write_bytes(b"")

    scenes = root / "V3Cmeta/scenesV3C/scenes"
    scenes.mkdir(parents=True)
    (scenes / "00001.mp4.scenes.txt").write_text(SCENES)
    (scenes / "00002.mp4.scenes.txt").write_text(SCENES)
    (scenes / "extractkeyframe.py").write_text("print('not a scene file')")

    (root / "MVKmeta").mkdir()
    with tarfile.open(root / "MVKmeta/scenesMVK12.tgz", "w:gz") as archive:
        for name in ("Ambon_Apr2012_0001", "Ambon_Apr2012_0002"):
            data = SCENES.encode()
            member = tarfile.TarInfo(f"scenes/{name}.mp4.scenes.txt")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))

    monkeypatch.setattr(Config, "SOURCE_ROOT", str(root))
    monkeypatch.setattr(Config, "WORK_DIR", str(tmp_path / "work"))
    for key in ("V3C", "MVK", "LHE"):
        for kind in ("VIDEOS", "SCENES", "INFO"):
            monkeypatch.delenv(f"CLIMB_{key}_{kind}", raising=False)
    return root


@pytest.mark.parametrize("name, expected", [
    ("00001.mp4.scenes.txt", "00001"),
    ("00001.json", "00001"),
    ("10536.info.json", "10536"),
    ("scenes/Ambon_Apr2012_0001.mp4.scenes.txt", "Ambon_Apr2012_0001"),
    ("LHE01.mp4", "LHE01"),
])
def test_entry_id_covers_every_naming_scheme(name, expected):
    assert datasets.entry_id(name) == expected


def test_extracted_directory_and_archive_read_the_same(source_root):
    v3c, mvk = datasets.load("V3C"), datasets.load("MVK")
    assert not v3c.scenes.is_archive and mvk.scenes.is_archive

    assert v3c.scenes.ids() == {"00001", "00002"}
    assert mvk.scenes.ids() == {"Ambon_Apr2012_0001", "Ambon_Apr2012_0002"}
    assert v3c.scenes.read_many(["00002"]) == {"00002": SCENES}
    assert mvk.scenes.read_many(["Ambon_Apr2012_0002"]) == {"Ambon_Apr2012_0002": SCENES}


def test_the_first_existing_candidate_wins(source_root):
    """datasets.json lists the MVK archive before the folder; the folder is only the fallback."""
    scenes = source_root / "MVKmeta/scenes"
    scenes.mkdir()
    (scenes / "Ambon_Apr2012_0001.mp4.scenes.txt").write_text(SCENES)
    assert datasets.load("MVK").scenes.is_archive

    (source_root / "MVKmeta/scenesMVK12.tgz").unlink()
    assert not datasets.load("MVK").scenes.is_archive


def test_only_video_files_count_as_videos(source_root):
    assert set(datasets.load("MVK").video_files()) == {"Ambon_Apr2012_0001", "Ambon_Apr2012_0002"}


def test_a_collection_without_scene_files_is_not_selected(source_root):
    """LHE has neither videos nor scenes in the fake layout, so `all` must not try to ingest it."""
    assert [d.name for d in datasets.selected()] == ["V3C", "MVK"]
    assert datasets.load("LHE").scenes is None
    assert datasets.load("lhe").name == "LHE"


def test_an_unknown_collection_is_an_error(source_root):
    with pytest.raises(KeyError):
        datasets.load("V3C2")


def test_overrides_absolute_and_relative(source_root, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("CLIMB_V3C_VIDEOS", str(elsewhere))
    monkeypatch.setenv("CLIMB_MVK_VIDEOS", "VBS/videos/v3c")
    assert datasets.load("V3C").videos == elsewhere
    assert datasets.load("MVK").videos == source_root / "VBS/videos/v3c"


def test_no_source_root_means_nothing_resolves(source_root, monkeypatch):
    monkeypatch.setattr(Config, "SOURCE_ROOT", "")
    v3c = datasets.load("V3C")
    assert v3c.videos is None and v3c.scenes is None and not v3c.has_videos


def test_a_fetched_copy_wins_over_the_collection(source_root, tmp_path):
    assert datasets.find_source_video("00001", "V3C") == source_root / "VBS/videos/v3c/00001.mp4"

    fetched = tmp_path / "work/raw/00001.mp4"
    fetched.parent.mkdir(parents=True)
    fetched.write_bytes(b"")
    assert datasets.find_source_video("00001", "V3C") == fetched
    assert datasets.find_source_video("99999", "V3C") is None


def test_transnet_scene_text_parses(source_root):
    boundaries, warnings = parse_shot_boundaries(SCENES, "00001 scene file", fps=25.0)
    assert [(b.start_frame, b.end_frame) for b in boundaries] == [(0, 152), (153, 199), (200, 249)]
    assert boundaries[1].start_ms == 6120
    assert warnings == []


def test_decode_writes_candidates_and_audio_and_no_video(source_root):
    """The sources are already 480x270, so there is no transcode output any more."""
    command = build_command("00001", "/src/00001.mp4", fps=25.0, with_audio=True)
    assert "libx264" not in command and "h264_nvenc" not in command
    assert command.count("-map") == 2
    assert command[-1].endswith("00001.opus")

    silent = build_command("00001", "/src/00001.mp4", fps=25.0, with_audio=False)
    assert silent.count("-map") == 1 and "0:a:0" not in silent
