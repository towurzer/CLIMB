"""
One transcript per video, however many ASR runs reach it. The pending list is read once at the start
of a run, and a run on another shard layout or with other settings can get to the same video.
"""

import threading
import time

import pytest

from pipeline import asr
from pipeline.asr import Segment

pytestmark = pytest.mark.integration

COLLECTION = "ASR-TEST"
FIRST, SECOND = "asr-test-1", "asr-test-2"


@pytest.fixture
def connect(db):
    from db.connection import connect_to_database

    connections = []

    def make():
        connections.append(connect_to_database())
        return connections[-1]

    yield make
    for connection in connections:
        connection.close()


@pytest.fixture
def videos(connect, monkeypatch, tmp_path):
    audio = tmp_path / "audio.opus"
    audio.touch()
    monkeypatch.setattr(asr, "load_model", lambda dev: object())
    monkeypatch.setattr(asr.paths, "audio_path", lambda video_id: audio)
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO videos (video_id, collection, fps, duration_ms, has_audio)
            VALUES (%s, %s, 25, 60000, TRUE), (%s, %s, 25, 60000, TRUE)
        """, (FIRST, COLLECTION, SECOND, COLLECTION))
    conn.commit()
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM videos WHERE collection = %s", (COLLECTION,))
    conn.commit()


def _store(conn, video_id, text="theirs"):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO transcript_segment (video_id, start_ms, end_ms, text) "
                    "VALUES (%s, 0, 1000, %s)", (video_id, text))


def _texts(conn, video_id):
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM transcript_segment WHERE video_id = %s ORDER BY start_ms",
                    (video_id,))
        rows = [r[0] for r in cur.fetchall()]
    conn.rollback()
    return rows


def test_a_transcript_stored_during_the_transcription_is_kept(videos, connect, monkeypatch):
    other = connect()

    def transcribe(model, audio):
        _store(other, FIRST)
        other.commit()
        return [Segment(500, 1500, "ours")], "en"

    monkeypatch.setattr(asr, "transcribe", transcribe)
    result = asr.asr_pending(connect(), collection=COLLECTION, limit=1)
    assert result.already == 1 and result.videos == 0
    assert _texts(other, FIRST) == ["theirs"]


def test_a_video_another_run_finished_is_not_transcribed(videos, connect, monkeypatch):
    other = connect()
    calls = []

    def transcribe(model, audio):
        calls.append(audio)
        _store(other, SECOND)
        other.commit()
        return [Segment(0, 1000, "ours")], "en"

    monkeypatch.setattr(asr, "transcribe", transcribe)
    result = asr.asr_pending(connect(), collection=COLLECTION)
    assert len(calls) == 1
    assert result.videos == 1 and result.already == 1
    assert _texts(other, SECOND) == ["theirs"]


def test_two_runs_storing_at_once_keep_one_transcript(videos, connect, monkeypatch):
    """The second writer has already passed the first check, and the first has not committed."""
    first_run, second_run, watcher = connect(), connect(), connect()
    monkeypatch.setattr(asr, "transcribe", lambda model, audio: ([Segment(500, 1500, "ours")], "en"))

    with first_run.cursor() as cur:
        cur.execute(asr.LOCK_VIDEO, (FIRST,))
    _store(first_run, FIRST)

    results = []
    thread = threading.Thread(target=lambda: results.append(
        asr.asr_pending(second_run, collection=COLLECTION, limit=1)))
    thread.start()

    deadline = time.monotonic() + 30
    waiting = False
    while not waiting and time.monotonic() < deadline:
        with watcher.cursor() as cur:
            cur.execute("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = %s",
                        (second_run.get_backend_pid(),))
            waiting = bool(cur.fetchone()[0])
        watcher.rollback()
        time.sleep(0.05)
    first_run.commit()
    thread.join(timeout=30)

    assert waiting, "the second run never waited for the first one's lock"
    assert results and results[0].already == 1
    assert _texts(first_run, FIRST) == ["theirs"]


def test_a_repeated_time_range_keeps_the_first_segment(videos, connect, monkeypatch):
    monkeypatch.setattr(asr, "transcribe", lambda model, audio: (
        [Segment(0, 1000, "first"), Segment(0, 1000, "second"), Segment(1000, 2000, "third")], "en"))
    conn = connect()
    asr.asr_pending(conn, collection=COLLECTION, limit=1)
    assert _texts(conn, FIRST) == ["first", "third"]
