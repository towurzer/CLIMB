"""How caption hits become keyframe rows, for shot-level and per-keyframe captions."""
from retrieval.retrievers import _with_uncaptioned_siblings


def test_shot_level_captions_share_the_rank_with_the_whole_shot():
    # `caption` (CAPTION_SCOPE shot): only kf 0 of each shot has a caption, so the hit carries
    # the shot's other frames with it, right after it.
    rows = [(10, 100), (20, 200)]
    uncaptioned = {10: [101, 102], 20: [201]}
    assert _with_uncaptioned_siblings(rows, uncaptioned, 10) == [
        (10, 100), (10, 101), (10, 102), (20, 200), (20, 201)]


def test_per_keyframe_captions_rank_each_frame_by_its_own_caption():
    # `caption --all-keyframes`: nothing is uncaptioned, so the hits come back as ranked and a shot
    # with three matching frames is not repeated three times over.
    rows = [(10, 101), (20, 200), (10, 100), (10, 102)]
    assert _with_uncaptioned_siblings(rows, {}, 10) == rows


def test_a_shot_expands_once_at_its_best_hit():
    # Mixed: shot 10 has two captioned frames and one without; the uncaptioned one follows the
    # better-ranked hit only.
    rows = [(10, 101), (20, 200), (10, 100)]
    uncaptioned = {10: [102]}
    assert _with_uncaptioned_siblings(rows, uncaptioned, 10) == [
        (10, 101), (10, 102), (20, 200), (10, 100)]


def test_limit_cuts_the_expanded_list():
    rows = [(10, 100), (20, 200)]
    uncaptioned = {10: [101, 102, 103]}
    assert _with_uncaptioned_siblings(rows, uncaptioned, 3) == [(10, 100), (10, 101), (10, 102)]
