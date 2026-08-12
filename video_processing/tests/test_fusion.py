"""
Reciprocal rank fusion.

Small enough to check arithmetically, which is worth doing: the weights are the main tuning knob
in the whole search, and a fusion that quietly ignores one is indistinguishable from a retriever
that quietly returns nothing.
"""

import pytest
from retrieval.fusion import RRF_K, FusedResult, fuse


def scores(results):
    return {r.scene_id: r.score for r in results}


# --- the arithmetic ----------------------------------------------------------------------------

def test_single_list_scores_by_rank():
    results = fuse({"visual": [1, 2, 3]}, {"visual": 1.0})
    assert scores(results) == pytest.approx({
        1: 1.0 / (RRF_K + 1),
        2: 1.0 / (RRF_K + 2),
        3: 1.0 / (RRF_K + 3),
    })


def test_weight_scales_the_contribution():
    weighted = fuse({"ocr": [1]}, {"ocr": 4.0})
    plain = fuse({"visual": [1]}, {"visual": 1.0})
    assert weighted[0].score == pytest.approx(4 * plain[0].score)


def test_contributions_from_several_retrievers_add_up():
    results = fuse({"visual": [1], "ocr": [1]}, {"visual": 1.0, "ocr": 4.0})
    assert results[0].score == pytest.approx(5.0 / (RRF_K + 1))


def test_missing_weight_defaults_to_one():
    results = fuse({"mystery": [1]}, {})
    assert results[0].score == pytest.approx(1.0 / (RRF_K + 1))


def test_zero_and_negative_weights_disable_a_retriever():
    # Turning a signal off must remove it, not merely shrink it -- a disabled retriever still
    # naming a scene would keep it in the results with a phantom signal.
    for weight in (0.0, -1.0):
        results = fuse({"visual": [1], "ocr": [2]}, {"visual": 1.0, "ocr": weight})
        assert [r.scene_id for r in results] == [1]
        assert "ocr" not in results[0].signals


def test_heavy_ocr_weight_outranks_a_better_visual_rank():
    # The reason OCR carries weight 4: an exact proper-noun match beats a good visual guess.
    # 4/(60+5) = 0.0615 vs 1/(60+1) = 0.0164.
    results = fuse(
        {"visual": [1, 2, 3, 4, 9], "ocr": [5, 6, 7, 8, 9]},
        {"visual": 1.0, "ocr": 4.0},
    )
    assert results[0].scene_id == 9


# --- ordering ----------------------------------------------------------------------------------

def test_results_are_ordered_by_score():
    results = fuse({"visual": [3, 1, 2]}, {"visual": 1.0})
    assert [r.scene_id for r in results] == [3, 1, 2]


def test_ties_break_on_scene_id_for_a_stable_ranking():
    # Two retrievers, mirror-image rankings: both scenes score identically.
    results = fuse({"visual": [7, 4], "caption": [4, 7]}, {"visual": 1.0, "caption": 1.0})
    assert results[0].score == pytest.approx(results[1].score)
    assert [r.scene_id for r in results] == [4, 7]


def test_limit_truncates():
    results = fuse({"visual": [1, 2, 3, 4]}, {"visual": 1.0}, limit=2)
    assert [r.scene_id for r in results] == [1, 2]


# --- signals and keyframes -----------------------------------------------------------------------

def test_signals_record_which_retriever_found_it_and_where():
    results = fuse({"visual": [1, 2], "ocr": [2, 1]}, {"visual": 1.0, "ocr": 1.0})
    by_scene = {r.scene_id: r.signals for r in results}
    assert by_scene[1] == {"visual": 1, "ocr": 2}
    assert by_scene[2] == {"visual": 2, "ocr": 1}


def test_a_scene_found_by_one_retriever_has_one_signal():
    results = fuse({"visual": [1], "ocr": [2]}, {"visual": 1.0, "ocr": 1.0})
    assert {r.scene_id: set(r.signals) for r in results} == {1: {"visual"}, 2: {"ocr"}}


def test_bare_scene_ids_and_tuples_both_accepted():
    results = fuse({"visual": [(1, 100)], "ocr": [2]}, {"visual": 1.0, "ocr": 1.0})
    by_scene = {r.scene_id: r.keyframe_id for r in results}
    assert by_scene == {1: 100, 2: None}


def test_first_retriever_to_name_a_keyframe_decides():
    # Retriever order is load-bearing: the visual retriever runs first because its keyframe is the
    # one that actually matched, where OCR only identifies the scene.
    results = fuse({"visual": [(1, 100)], "ocr": [(1, 999)]}, {"visual": 1.0, "ocr": 4.0})
    assert results[0].keyframe_id == 100


def test_a_disabled_retriever_cannot_supply_the_keyframe():
    results = fuse({"ocr": [(1, 999)], "visual": [(1, 100)]}, {"ocr": 0.0, "visual": 1.0})
    assert results[0].keyframe_id == 100


# --- empty input ---------------------------------------------------------------------------------

def test_no_lists_yields_no_results():
    assert fuse({}, {"visual": 1.0}) == []


def test_empty_lists_yield_no_results():
    assert fuse({"visual": [], "ocr": []}, {"visual": 1.0, "ocr": 4.0}) == []


def test_results_are_fused_results():
    results = fuse({"visual": [1]}, {"visual": 1.0})
    assert isinstance(results[0], FusedResult)
