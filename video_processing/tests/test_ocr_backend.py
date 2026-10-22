"""
The OCR backend adapters.

Both engines are wrapped so `ocr_pending` never learns which one it got. The shapes they return
are genuinely different -- paddle returns one result per image holding parallel text and
confidence lists, rapidocr returns a flat list, `None` instead of an empty one, and a confidence
as a *string* -- so the adapters are exactly where a silent failure would live.

The engines themselves are faked here: this is about the translation, and neither model belongs in
a unit test. The real engines are exercised by the smoke test.
"""

import pytest
from config import Config
from pipeline.ocr import BACKENDS, PaddleReader, RapidReader, extract_text, load_reader


class FakeReader:
    def __init__(self, lines):
        self._lines = lines

    def read(self, image_path):
        return self._lines


def paddle_reader(pages):
    reader = object.__new__(PaddleReader)
    reader._engine = type("E", (), {"predict": lambda self, path: pages})()
    return reader


def rapid_reader(result):
    reader = object.__new__(RapidReader)
    reader._engine = lambda path: (result, 0.0)
    return reader


# --- extract_text ------------------------------------------------------------------------------

def test_confident_lines_are_joined():
    assert extract_text(FakeReader([("BOULANGERIE", 0.9), ("DUPONT", 0.8)]), "x") \
        == "BOULANGERIE DUPONT"


def test_lines_below_the_confidence_floor_are_dropped():
    floor = Config.OCR_MIN_CONFIDENCE
    reader = FakeReader([("REAL", floor + 0.1), ("n0ise", floor - 0.1)])
    assert extract_text(reader, "x") == "REAL"


def test_confidence_floor_is_inclusive():
    reader = FakeReader([("EXACT", Config.OCR_MIN_CONFIDENCE)])
    assert extract_text(reader, "x") == "EXACT"


def test_no_lines_gives_empty_string():
    # Must be "" and not None: the caller stores this, and a row is what stops the keyframe
    # being re-OCR'd forever.
    assert extract_text(FakeReader([]), "x") == ""


def test_blank_and_whitespace_lines_are_dropped():
    reader = FakeReader([("", 0.99), ("   ", 0.99), ("REAL", 0.99)])
    assert extract_text(reader, "x") == "REAL"


def test_surrounding_whitespace_is_stripped():
    assert extract_text(FakeReader([("  SIGN  ", 0.9)]), "x") == "SIGN"


# --- the paddle adapter --------------------------------------------------------------------------

def test_paddle_texts_and_scores_are_paired():
    pages = [{"rec_texts": ["HELLO", "WORLD"], "rec_scores": [0.9, 0.8], "rec_polys": []}]
    assert paddle_reader(pages).read("x") == [("HELLO", 0.9), ("WORLD", 0.8)]


def test_paddle_frame_without_text_is_no_lines():
    assert paddle_reader([{"rec_texts": [], "rec_scores": []}]).read("x") == []


def test_paddle_none_result_is_no_lines():
    assert paddle_reader(None).read("x") == []


# --- the rapidocr adapter ------------------------------------------------------------------------

def test_rapidocr_lines_are_unpacked():
    result = [[[[0, 0]], "BOULANGERIE", "0.9085"], [[[0, 0]], "DUPONT", "0.7354"]]
    assert rapid_reader(result).read("x") == [("BOULANGERIE", 0.9085), ("DUPONT", 0.7354)]


def test_rapidocr_string_confidence_is_coerced_to_float():
    # It really does return a string. Comparing that to OCR_MIN_CONFIDENCE raises TypeError,
    # so this coercion is the whole reason the adapter exists.
    (_text, confidence), = rapid_reader([[[[0, 0]], "SIGN", "0.87"]]).read("x")
    assert isinstance(confidence, float)
    assert confidence == pytest.approx(0.87)


def test_rapidocr_none_result_is_no_lines():
    # A frame with no text at all returns None, not [].
    assert rapid_reader(None).read("x") == []


def test_rapidocr_and_extract_text_compose():
    reader = rapid_reader([[[[0, 0]], "KEEP", "0.9"], [[[0, 0]], "DROP", "0.1"]])
    assert extract_text(reader, "x") == "KEEP"


# --- backend selection ---------------------------------------------------------------------------

def test_pinned_backend_is_honoured(monkeypatch):
    monkeypatch.setattr(Config, "OCR_BACKEND", "rapidocr")
    assert isinstance(load_reader("cpu"), RapidReader)


def test_unknown_backend_is_rejected(monkeypatch):
    # Misspelling the env var must fail loudly rather than silently falling back.
    monkeypatch.setattr(Config, "OCR_BACKEND", "tesseract")
    with pytest.raises(ValueError, match="tesseract"):
        load_reader("cpu")


def test_auto_prefers_paddle_when_it_imports(monkeypatch, caplog):
    monkeypatch.setattr(Config, "OCR_BACKEND", "auto")
    monkeypatch.setitem(BACKENDS, "paddle", lambda dev: "paddle-reader")
    assert load_reader("cpu") == "paddle-reader"
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_auto_falls_back_when_paddle_is_absent(monkeypatch, caplog):
    monkeypatch.setattr(Config, "OCR_BACKEND", "auto")
    monkeypatch.setitem(BACKENDS, "paddle", _raise_import_error)
    assert isinstance(load_reader("cpu"), RapidReader)
    # The fallback reads worse, so it is a warning carrying paddle's import error.
    warning, = [r for r in caplog.records if r.levelname == "WARNING"]
    assert "rapidocr" in warning.message and "paddle (not installed)" in warning.message


def test_no_backend_at_all_raises_with_install_instructions(monkeypatch):
    monkeypatch.setattr(Config, "OCR_BACKEND", "auto")
    monkeypatch.setitem(BACKENDS, "paddle", _raise_import_error)
    monkeypatch.setitem(BACKENDS, "rapidocr", _raise_import_error)
    with pytest.raises(ImportError, match="rapidocr-onnxruntime"):
        load_reader("cpu")


def _raise_import_error(dev):
    raise ImportError("not installed")
