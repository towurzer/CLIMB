"""
Which pieces of a query are tried against the V3C tags, and how two video-level signals combine.

The database normalises every term the same way as the tags (migration 012), so these only pin
which words and word runs are offered. A missing run means a multi-word tag ("fish eye", "first
dance") can never match; a stopword offered on its own would match tags like "the".
"""

from retrieval.fusion import FusedResult, apply_video_prior
from retrieval.query_parser import tag_terms


def test_content_words_are_offered_and_stopwords_are_not():
    terms = tag_terms("a bmx rider in paris")
    assert {"bmx", "rider", "paris"} <= set(terms)
    assert "a" not in terms and "in" not in terms


def test_runs_of_words_are_offered_for_multi_word_tags():
    terms = tag_terms("a fish eye shot of a first dance")
    assert "fish eye" in terms and "first dance" in terms


def test_runs_may_hold_stopwords_but_not_only_stopwords():
    """'of monsters and men' is a tag; 'of a' never is."""
    terms = tag_terms("of monsters and men")
    assert "of monsters and men" in terms
    assert "and" not in terms
    assert not any(all(w in {"of", "a", "the", "in"} for w in t.split()) for t in tag_terms("of a the in"))


def test_runs_stop_at_four_words():
    assert not any(len(t.split()) > 4 for t in tag_terms("one two three four five six"))


def test_words_come_with_and_without_a_plural_s():
    assert {"bike", "bikes"} <= set(tag_terms("a bike"))
    assert {"bikes", "bike"} <= set(tag_terms("bikes"))
    # Short words are left alone rather than turned into "bu" or "gas" into "ga".
    assert "ga" not in tag_terms("gas")


def test_words_are_lowercased_and_offered_once():
    terms = tag_terms("BMX bmx Paris")
    assert terms.count("bmx") == 1 and "paris" in terms


def test_no_words_no_terms():
    assert tag_terms("") == [] and tag_terms("!!! ...") == []


def _fused(*ids):
    return [FusedResult(keyframe_id=k, scene_id=k, score=1.0 / (60 + i), signals={}, contributions={})
            for i, k in enumerate(ids, start=1)]


def test_two_video_signals_both_count_before_the_cut():
    """
    Info and tags each re-order what the frame signals found. Cutting to the limit after the
    first one would drop frames the second one would have lifted, so the engine cuts once, after
    both (engine._retrieve); here, both priors on the same list add up.
    """
    fused = _fused(1, 2, 3)
    video_of = {1: "a", 2: "b", 3: "c"}
    fused = apply_video_prior(fused, video_of, ["c"], 1.0, name="info")
    fused = apply_video_prior(fused, video_of, ["c"], 1.0, name="tags")
    assert fused[0].keyframe_id == 3
    assert set(fused[0].signals) == {"info", "tags"}
