"""
Reciprocal rank fusion.

Small enough to check arithmetically, which is worth doing: the weights are the main tuning knob
in the whole search, and a fusion that quietly ignores one is indistinguishable from a retriever
that quietly returns nothing.

Fusion keys on *keyframe*. Results are one row per keyframe, so a shot whose frames fill the top
of the page is telling you it is the answer; collapsing to one row per scene used to hide exactly
that. Every entry is therefore a (scene_id, keyframe_id) pair.
"""

import pytest
from retrieval.fusion import RRF_K, FusedResult, fuse

# (scene, keyframe) pairs. Scene 1 owns keyframes 100/101, scene 2 owns 200, and so on, so a test
# can talk about "two keyframes of the same shot" without repeating the arithmetic.
A1, A2 = (1, 100), (1, 101)
B1 = (2, 200)
C1 = (3, 300)


def scores(results):
    return {r.keyframe_id: r.score for r in results}


def ids(results):
    return [r.keyframe_id for r in results]


# --- the arithmetic ----------------------------------------------------------------------------

def test_single_list_scores_by_rank():
    results = fuse({"visual": [A1, B1, C1]}, {"visual": 1.0})
    assert scores(results) == pytest.approx({
        100: 1.0 / (RRF_K + 1),
        200: 1.0 / (RRF_K + 2),
        300: 1.0 / (RRF_K + 3),
    })


def test_weight_scales_the_contribution():
    weighted = fuse({"ocr": [A1]}, {"ocr": 4.0})
    plain = fuse({"visual": [A1]}, {"visual": 1.0})
    assert weighted[0].score == pytest.approx(4 * plain[0].score)


def test_contributions_from_several_retrievers_add_up():
    results = fuse({"visual": [A1], "ocr": [A1]}, {"visual": 1.0, "ocr": 4.0})
    assert results[0].score == pytest.approx(5.0 / (RRF_K + 1))


def test_missing_weight_defaults_to_one():
    results = fuse({"mystery": [A1]}, {})
    assert results[0].score == pytest.approx(1.0 / (RRF_K + 1))


def test_zero_and_negative_weights_disable_a_retriever():
    # Turning a signal off must remove it, not merely shrink it -- a disabled retriever still
    # naming a keyframe would keep it in the results with a phantom signal.
    for weight in (0.0, -1.0):
        results = fuse({"visual": [A1], "ocr": [B1]}, {"visual": 1.0, "ocr": weight})
        assert ids(results) == [100]
        assert "ocr" not in results[0].signals


def test_heavy_ocr_weight_outranks_a_better_visual_rank():
    # The reason OCR carries weight 4: an exact proper-noun match beats a good visual guess.
    # 4/(60+5) = 0.0615 vs 1/(60+1) = 0.0164.
    filler = [(9, 900 + i) for i in range(4)]
    results = fuse(
        {"visual": filler + [C1], "ocr": [(8, 800 + i) for i in range(4)] + [C1]},
        {"visual": 1.0, "ocr": 4.0},
    )
    assert results[0].keyframe_id == 300


# --- one row per keyframe ------------------------------------------------------------------------

def test_two_keyframes_of_one_scene_are_two_rows():
    """
    The point of keying on keyframes. Both frames of scene 1 survive, each with its own rank,
    instead of the shot appearing once and the second frame being discarded.
    """
    results = fuse({"visual": [A1, A2, B1]}, {"visual": 1.0})
    assert ids(results) == [100, 101, 200]
    assert [r.scene_id for r in results] == [1, 1, 2]


def test_the_same_keyframe_named_twice_by_one_retriever_accumulates():
    results = fuse({"ocr": [A1, A1, B1]}, {"ocr": 4.0})
    keyframe = next(r for r in results if r.keyframe_id == 100)

    assert keyframe.score == pytest.approx(4.0 / (RRF_K + 1) + 4.0 / (RRF_K + 2))
    assert sum(keyframe.contributions.values()) == pytest.approx(keyframe.score)
    # The best rank, not the last one -- that is where the retriever actually placed it.
    assert keyframe.signals == {"ocr": 1}


def test_entries_without_a_keyframe_are_dropped():
    # Every retriever resolves to keyframes before returning; the scene-level ones expand across
    # their scene's keyframes. A None here means a scene with no keyframe rows at all.
    results = fuse({"visual": [A1, (4, None)]}, {"visual": 1.0})
    assert ids(results) == [100]


def test_the_scene_rides_along_with_each_keyframe():
    results = fuse({"visual": [A2]}, {"visual": 1.0})
    assert results[0].scene_id == 1
    assert results[0].keyframe_id == 101


# --- ordering ----------------------------------------------------------------------------------

def test_results_are_ordered_by_score():
    results = fuse({"visual": [C1, A1, B1]}, {"visual": 1.0})
    assert ids(results) == [300, 100, 200]


def test_ties_break_on_keyframe_id_for_a_stable_ranking():
    # Two retrievers, mirror-image rankings: both keyframes score identically.
    results = fuse({"visual": [C1, B1], "caption": [B1, C1]},
                   {"visual": 1.0, "caption": 1.0})
    assert results[0].score == pytest.approx(results[1].score)
    assert ids(results) == [200, 300]


def test_limit_truncates():
    results = fuse({"visual": [A1, A2, B1, C1]}, {"visual": 1.0}, limit=2)
    assert ids(results) == [100, 101]


# --- signals and contributions --------------------------------------------------------------------

def test_signals_record_which_retriever_found_it_and_where():
    results = fuse({"visual": [A1, B1], "ocr": [B1, A1]}, {"visual": 1.0, "ocr": 1.0})
    by_keyframe = {r.keyframe_id: r.signals for r in results}
    assert by_keyframe[100] == {"visual": 1, "ocr": 2}
    assert by_keyframe[200] == {"visual": 2, "ocr": 1}


def test_a_keyframe_found_by_one_retriever_has_one_signal():
    results = fuse({"visual": [A1], "ocr": [B1]}, {"visual": 1.0, "ocr": 1.0})
    assert {r.keyframe_id: set(r.signals) for r in results} == {100: {"visual"}, 200: {"ocr"}}


def test_contributions_split_the_score_by_retriever():
    results = fuse({"visual": [A1, B1], "ocr": [B1, A1]}, {"visual": 1.0, "ocr": 4.0})
    by_keyframe = {r.keyframe_id: r.contributions for r in results}
    assert by_keyframe[100] == pytest.approx({"visual": 1.0 / (RRF_K + 1),
                                              "ocr": 4.0 / (RRF_K + 2)})
    assert by_keyframe[200] == pytest.approx({"visual": 1.0 / (RRF_K + 2),
                                              "ocr": 4.0 / (RRF_K + 1)})
    # They are shares of the score, so they add back up to it.
    for result in results:
        assert sum(result.contributions.values()) == pytest.approx(result.score)


def test_the_biggest_contributor_is_not_always_the_best_rank():
    """
    The whole reason contributions are recorded separately from ranks.

    OCR is weighted 4x, so it can be the reason a keyframe surfaced while sitting far below the
    visual retriever in rank. Ordering the UI's signal badges by rank would name the wrong encoder.
    """
    ocr_rank, visual_rank = 20, 5
    # Distinct filler keyframes ahead of the one under test, so it lands on the intended rank in
    # each list without either retriever repeating it.
    results = fuse({"visual": [(10, 1000 + i) for i in range(visual_rank - 1)] + [A1],
                    "ocr": [(20, 2000 + i) for i in range(ocr_rank - 1)] + [A1]},
                   {"visual": 1.0, "ocr": 4.0})
    keyframe = next(r for r in results if r.keyframe_id == 100)

    assert keyframe.signals == {"visual": visual_rank, "ocr": ocr_rank}
    assert min(keyframe.signals, key=keyframe.signals.get) == "visual"
    assert max(keyframe.contributions, key=keyframe.contributions.get) == "ocr"


# --- empty input ---------------------------------------------------------------------------------

def test_no_lists_yields_no_results():
    assert fuse({}, {"visual": 1.0}) == []


def test_empty_lists_yield_no_results():
    assert fuse({"visual": [], "ocr": []}, {"visual": 1.0, "ocr": 4.0}) == []


def test_results_are_fused_results():
    results = fuse({"visual": [A1]}, {"visual": 1.0})
    assert isinstance(results[0], FusedResult)
