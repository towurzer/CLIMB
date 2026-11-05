"""
What decode does with a short extraction. The course copies of 00016 and 00024 have a 9 MiB hole of
zeros, the copies on the collection's HDD are intact, so no video in the full database carries the
flag and this needs a file of its own.
"""

import shutil
import subprocess

import pytest

from config import Config
from pipeline.decode import decode_video

DURATION_S = 30
FPS = 25

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


@pytest.fixture(scope="module")
def intact(tmp_path_factory):
    path = tmp_path_factory.mktemp("source") / "intact.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"testsrc2=size=320x180:rate={FPS}:duration={DURATION_S}",
                    "-c:v", "libx264", "-g", str(FPS), "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@pytest.fixture
def damaged(intact, tmp_path):
    """The same video with a fifth of the file, in the middle of the stream, zeroed."""
    data = bytearray(intact.read_bytes())
    start, length = int(len(data) * 0.4), int(len(data) * 0.2)
    data[start:start + length] = bytes(length)
    path = tmp_path / "damaged.mp4"
    path.write_bytes(data)
    return path


@pytest.fixture(autouse=True)
def work_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "WORK_DIR", str(tmp_path / "work"))


def _decode(source, duration_s=DURATION_S):
    return decode_video("00001", source, FPS, duration_ms=duration_s * 1000, with_audio=False,
                        force=True)


def test_an_intact_file_is_not_flagged(intact):
    result = _decode(intact)
    assert result.ok and not result.damaged


def test_a_damaged_file_is_indexed_and_flagged(intact, damaged):
    complete = _decode(intact).candidate_count
    result = _decode(damaged)
    assert result.ok and result.damaged
    assert 0 < result.candidate_count < complete


def test_a_short_extraction_without_decoder_errors_fails(intact):
    """A clean decode that comes up short is a full disk, not a broken file, and worth a retry."""
    result = _decode(intact, duration_s=2 * DURATION_S)
    assert not result.ok and not result.damaged
    assert "truncated extraction" in result.error
