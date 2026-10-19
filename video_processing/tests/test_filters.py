"""
Requirements combine at video level: text:, said: and tag: each narrow the set of videos that may
answer, and the next one is evaluated inside what is left. No database: the per-requirement
lookups are replaced by fakes that know which videos contain what.
"""

import pytest

from retrieval import engine as engine_module
from retrieval import retrievers
from retrieval.engine import SearchEngine, _interleave
from retrieval.query_parser import parse
from retrieval.retrievers import Scope

# video -> what can be found in it
OCR = {"v1": {"they kissed"}, "v2": {"they kissed"}, "v3": set()}
SPEECH = {"v1": {"hello darling"}, "v2": set(), "v3": {"hello darling"}}
TAGS = {"v1": {"wedding"}, "v2": {"wedding"}, "v3": {"bmx"}}


def _finder(table):
    def find(conn, value, scope):
        candidates = scope.only if scope.only is not None else table.keys()
        return {v for v in candidates if v not in scope.exclude and value in table[v]}
    return find


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(retrievers, "videos_with_ocr_phrase", _finder(OCR))
    monkeypatch.setattr(retrievers, "videos_with_speech", _finder(SPEECH))
    monkeypatch.setattr(retrievers, "videos_with_tag", _finder(TAGS))
    instance = SearchEngine.__new__(SearchEngine)  # no models, no database
    instance.conn = None
    return instance


def test_the_professors_example_needs_both_in_the_same_video(engine):
    scope, filters = engine._apply_filters(parse('said:"hello darling" text:"they kissed"'),
                                           Scope())
    assert scope.only == ("v1",)
    assert filters == [{"filter": 'text:"they kissed"', "videos": 2},
                       {"filter": 'said:"hello darling"', "videos": 1}]


def test_requirements_narrow_inside_an_explicit_video_restriction(engine):
    scope, _ = engine._apply_filters(parse('text:"they kissed"'), Scope(only=("v2", "v3")))
    assert scope.only == ("v2",)


def test_exclusions_still_apply(engine):
    scope, _ = engine._apply_filters(parse("tag:wedding"), Scope(exclude=("v1",)))
    assert scope.only == ("v2",)


def test_once_nothing_is_left_the_rest_report_zero(engine, monkeypatch):
    """The funnel names the requirement that emptied it; later ones are not even looked up."""
    looked_up = []
    tag = _finder(TAGS)
    monkeypatch.setattr(retrievers, "videos_with_tag",
                        lambda conn, value, scope: looked_up.append(value) or tag(conn, value, scope))

    scope, filters = engine._apply_filters(
        parse('text:"they kissed" said:"hello darling" tag:bmx tag:wedding'), Scope())
    assert scope.only == ()
    assert [(f["filter"], f["videos"]) for f in filters] == [
        ('text:"they kissed"', 2), ('said:"hello darling"', 1), ('tag:"bmx"', 0),
        ('tag:"wedding"', 0)]
    assert looked_up == ["bmx"]


def test_no_requirements_leave_the_scope_alone(engine):
    scope, filters = engine._apply_filters(parse("a man rides a bike"), Scope(collection="V3C"))
    assert scope == Scope(collection="V3C") and filters == []


def test_interleave_alternates_and_dedupes():
    a, b = [(1, 10), (1, 11), (2, 20)], [(3, 30), (1, 10)]
    assert _interleave([a, b], 10) == [(1, 10), (3, 30), (1, 11), (2, 20)]
    assert _interleave([a, b], 2) == [(1, 10), (3, 30)]
    assert _interleave([], 5) == []


def test_small_scopes_rank_exactly(monkeypatch):
    monkeypatch.setattr(engine_module.Config, "EXACT_SCOPE_MAX_VIDEOS", 2)
    assert Scope(only=("a", "b")).exact
    assert not Scope(only=("a", "b", "c")).exact
    assert not Scope().exact
