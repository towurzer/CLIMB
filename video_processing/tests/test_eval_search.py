"""
The evaluations' stand-in for a search (evaluation/search.py), which README 1.7's tables are ranked,
fused and scored with: if it ranked or cut differently from the engine, the tables would describe another
search.
"""

import numpy as np

from evaluation import search


def test_ranking_is_by_similarity_with_ties_on_the_index_cut_at_the_depth():
    similarities = [0.1, 0.5, 0.5, 0.2]
    assert search.ranked(similarities).tolist() == [1, 2, 3, 0]
    assert search.ranked(similarities, depth=2).tolist() == [1, 2]


def test_fusion_returns_frame_indices_in_rrf_order():
    """Frame 0 is second in one list and first in the other, frame 2 first in one, frame 1 second in one."""
    assert search.fused({"visual": [2, 0], "peskavlp": [0, 1]},
                        {"visual": 1.0, "peskavlp": 1.0}) == [0, 2, 1]


def test_a_perfect_ranking_has_average_precision_one():
    assert search.average_precision([0, 1, 2, 3], np.array([True, True, False, False])) == 1.0


def test_average_precision_counts_the_ranks_of_the_hits():
    """Hits at ranks 2 and 4: (1/2 + 2/4) / 2."""
    assert search.average_precision([0, 1, 2, 3], np.array([False, True, False, True])) == 0.5


def test_a_relevant_frame_the_ranking_leaves_out_counts_as_never_found():
    """Frame 0 at rank 1, frame 1 not returned: (1/1 + 0) / 2."""
    assert search.average_precision([0], np.array([True, True])) == 0.5


def test_a_class_without_frames_scores_zero_rather_than_dividing_by_zero():
    assert search.average_precision([0, 1], np.array([False, False])) == 0.0


def test_a_frame_neither_model_returns_is_not_fused_in():
    """As in the engine each model hands over its `depth` best, so frame 2, third for both, is gone."""
    found = search.variants({"visual": np.array([0.9, 0.5, 0.1]), "peskavlp": np.array([0.5, 0.9, 0.1])},
                            (1.0,), depth=2)
    assert found["SigLIP2"].tolist() == [0, 1] and found["PeskaVLP"].tolist() == [1, 0]
    assert found["both, 1.0"] == [0, 1]
