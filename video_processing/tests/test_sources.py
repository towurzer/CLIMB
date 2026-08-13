"""
Which retrievers a request's `sources` list permits.

Pure mapping, but worth pinning: getting it wrong does not raise, it just quietly searches the
wrong things, and "why did unticking OCR change nothing" is a miserable thing to debug live.
"""

from retrieval.engine import SOURCE_RETRIEVERS, _allowed_retrievers


def test_no_sources_means_no_restriction():
    # None and [] are the same request: the UI omits the parameter when every box is ticked.
    assert _allowed_retrievers(None) is None
    assert _allowed_retrievers([]) is None


def test_a_source_maps_to_its_retrievers():
    assert _allowed_retrievers(["visual"]) == {"visual"}
    assert _allowed_retrievers(["asr"]) == {"transcript"}
    assert _allowed_retrievers(["visual", "caption"]) == {"visual", "caption"}


def test_names_are_case_and_space_insensitive():
    assert _allowed_retrievers([" OCR ", "Asr"]) == {"ocr", "transcript"}


def test_an_unknown_source_narrows_rather_than_raises():
    """A typo between the frontend list and this map should cost recall, never a 500."""
    assert _allowed_retrievers(["visual", "telepathy"]) == {"visual"}
    assert _allowed_retrievers(["telepathy"]) == set()


def test_phrase_retrievers_are_never_gated():
    """
    `text:"..."` and `said:"..."` only run when the query asks for them by name, and an operator
    you typed is an instruction rather than a source preference. Gating them here would turn an
    explicit phrase search into a silent zero-result dead end.
    """
    gated = {name for names in SOURCE_RETRIEVERS.values() for name in names}
    assert "ocr_phrase" not in gated
    assert "asr_phrase" not in gated


def test_every_source_maps_somewhere():
    """The frontend's SOURCES list in frontend/src/sources.js has to line up with these keys."""
    assert set(SOURCE_RETRIEVERS) == {"visual", "ocr", "asr", "caption"}
    assert all(names for names in SOURCE_RETRIEVERS.values())
