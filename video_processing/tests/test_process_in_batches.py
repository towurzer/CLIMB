"""
The decode -> select -> purge loop in `run`, with fake stages standing in for ffmpeg and the
database. What matters is that it ends, that it gets through everything, and that a video which
keeps failing neither blocks its batch nor gets retried forever.
"""

from types import SimpleNamespace

import pytest

from pipeline import runner


class FakePipeline:
    """Videos `v00`..: pending until selected. Ids in `broken` fail decode every time."""

    def __init__(self, count, broken=()):
        self.pending = [f"v{i:02d}" for i in range(count)]
        self.broken = set(broken)
        self.decoded, self.purges = set(), 0

    def _next(self, limit, exclude):
        return [v for v in self.pending if v not in set(exclude)][:limit]

    def decode(self, conn, collection=None, limit=None, exclude=()):
        results = []
        for video_id in self._next(limit, exclude):
            ok = video_id not in self.broken
            if ok:
                self.decoded.add(video_id)
            results.append(SimpleNamespace(video_id=video_id, ok=ok))
        return results

    def select(self, conn, collection=None, limit=None, exclude=(), video_ids=None):
        results = []
        candidates = self._next(limit or len(self.pending), exclude)
        if video_ids is not None:
            candidates = [v for v in candidates if v in set(video_ids)]
        for video_id in candidates:
            ok = video_id in self.decoded
            if ok:
                self.pending.remove(video_id)
            results.append(SimpleNamespace(video_id=video_id, ok=ok))
        return results

    def purge(self, conn, collection=None, keep_raw=False):
        self.purges += 1


@pytest.fixture
def fake(monkeypatch):
    def install(count, broken=()):
        pipeline = FakePipeline(count, broken)
        monkeypatch.setattr(runner, "decode_from_database", pipeline.decode)
        monkeypatch.setattr(runner, "select_from_database", pipeline.select)
        monkeypatch.setattr(runner, "purge", pipeline.purge)
        return pipeline
    return install


def test_everything_gets_through_in_batches(fake):
    pipeline = fake(25)
    failed = runner.process_in_batches(None, batch=10)
    assert failed == set() and pipeline.pending == []
    assert pipeline.purges == 3


def test_a_broken_video_is_skipped_not_retried_forever(fake):
    """More broken videos than fit in a batch: without the exclusion this loops forever."""
    broken = {"v00", "v01", "v02", "v03"}
    pipeline = fake(12, broken=broken)
    failed = runner.process_in_batches(None, batch=3)
    assert failed == broken
    assert pipeline.pending == sorted(broken)


def test_limit_caps_the_number_of_videos(fake):
    pipeline = fake(25)
    runner.process_in_batches(None, limit=7, batch=10)
    assert len(pipeline.pending) == 18


def test_limit_is_not_overshot_after_a_failure(fake):
    """v01 fails, so the first round selects 6 of 7; the second may only take 1 more."""
    pipeline = fake(25, broken={"v01"})
    runner.process_in_batches(None, limit=7, batch=10)
    assert 25 - len(pipeline.pending) == 7


def test_purge_can_be_left_out(fake):
    pipeline = fake(5)
    runner.process_in_batches(None, batch=10, purge_after=False)
    assert pipeline.purges == 0 and pipeline.pending == []
