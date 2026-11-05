"""
What the ASR stage asks faster-whisper for, sequential and batched. The two paths take different
arguments, and scenes are matched by the segments' time ranges, so the batched one must not fall
back to its default of one segment per chunk of speech.
"""

import sys
import types

from config import Config
from pipeline import asr


class FakeModel:
    def __init__(self):
        self.kwargs = None

    def transcribe(self, path, **kwargs):
        self.kwargs = kwargs
        return [], types.SimpleNamespace(language="en")


def _batch(monkeypatch, size):
    monkeypatch.setattr(asr, "Config", lambda: Config(ASR_BATCH=size))


def test_the_sequential_path_passes_no_batch_arguments(monkeypatch):
    _batch(monkeypatch, 1)
    model = FakeModel()
    asr.transcribe(model, "a.opus")
    assert "batch_size" not in model.kwargs
    assert "without_timestamps" not in model.kwargs


def test_the_batched_path_keeps_the_time_ranges(monkeypatch):
    _batch(monkeypatch, 16)
    model = FakeModel()
    asr.transcribe(model, "a.opus")
    assert model.kwargs["batch_size"] == 16
    assert model.kwargs["without_timestamps"] is False


def _load(monkeypatch, size):
    class Whisper:
        def __init__(self, name, device, compute_type):
            pass

    class Batched:
        def __init__(self, model):
            self.model = model

    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=Whisper, BatchedInferencePipeline=Batched))
    _batch(monkeypatch, size)
    return asr.load_model("cpu"), Whisper, Batched


def test_only_a_batch_above_one_loads_the_batched_pipeline(monkeypatch):
    model, whisper, _ = _load(monkeypatch, 1)
    assert isinstance(model, whisper)
    model, whisper, batched = _load(monkeypatch, 2)
    assert isinstance(model, batched) and isinstance(model.model, whisper)
