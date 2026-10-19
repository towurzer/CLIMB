"""
`run --limit` counts videos. It limits the stages that take in videos; the stages after them get no
limit of their own, because theirs would count keyframes and stop part way through what was selected.
"""

import pytest

import main


@pytest.fixture
def calls(monkeypatch):
    seen = {}

    def record(name):
        def stage(conn, **kwargs):
            seen[name] = kwargs.get("limit")
            return [], None
        return stage

    monkeypatch.setattr(main.runner, "process_in_batches",
                        lambda conn, limit=None, **kwargs: seen.update({"decode+select": limit}))
    for name in ("embed_pending", "ocr_pending", "caption_pending", "asr_pending"):
        monkeypatch.setattr(main, name, record(name))
    monkeypatch.setattr(main, "embed_text_pending", record("embed_text_pending"))
    return seen


def _run(*argv):
    main.dispatch(main.build_parser().parse_args(["run", *argv]), conn=object())


def test_the_limit_reaches_only_the_video_stages(calls):
    _run("--stages", "decode,select,embed,ocr,caption,asr,embed-text", "--limit", "17")
    assert calls == {"decode+select": 17, "embed_pending": None, "ocr_pending": None,
                     "caption_pending": None, "asr_pending": None, "embed_text_pending": None}


def test_a_limit_without_a_video_stage_is_refused(calls):
    with pytest.raises(SystemExit, match="counts videos"):
        _run("--stages", "embed,ocr", "--limit", "5")
    assert calls == {}


def test_no_limit_is_fine_either_way(calls):
    _run("--stages", "embed,ocr")
    assert calls == {"embed_pending": None, "ocr_pending": None}
