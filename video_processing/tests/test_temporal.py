"""
Temporal chain linking.

`link` is the one piece of retrieval with real algorithmic content, and every one of its rules
(strictly forward in time, strictly inside the window, no scene linking to itself, one chain per
anchor) is a rule a naive nested loop would get wrong in a way that only shows up as bad results.
"""

from retrieval.temporal import link


def hit(video_id, scene_id, ts_ms, score=1.0):
    return {"video_id": video_id, "scene_id": scene_id, "ts_ms": ts_ms, "score": score}


# --- degenerate input --------------------------------------------------------------------------

def test_single_stage_is_not_a_chain():
    assert link([[hit("v1", 1, 0)]], []) == []


def test_no_stages_is_not_a_chain():
    assert link([], []) == []


def test_gap_count_must_match_stage_count():
    stages = [[hit("v1", 1, 0)], [hit("v1", 2, 1000)]]
    assert link(stages, []) == []
    assert link(stages, [1000, 2000]) == []


def test_empty_stage_yields_no_chains():
    assert link([[hit("v1", 1, 0)], []], [10_000]) == []


# --- the basic link ----------------------------------------------------------------------------

def test_two_hits_inside_the_window_chain():
    stages = [[hit("v1", 1, 1_000, score=1.0)], [hit("v1", 2, 5_000, score=0.5)]]
    chains = link(stages, [10_000])
    assert len(chains) == 1
    assert [s["scene_id"] for s in chains[0].scenes] == [1, 2]
    assert chains[0].score == 1.5
    # Measured gap, not the window it had to fit inside.
    assert chains[0].gaps_ms == [4_000]


def test_hit_beyond_the_window_does_not_chain():
    stages = [[hit("v1", 1, 0)], [hit("v1", 2, 20_000)]]
    assert link(stages, [10_000]) == []


def test_window_upper_bound_is_exclusive():
    stages = [[hit("v1", 1, 0)], [hit("v1", 2, 10_000)]]
    assert link(stages, [10_000]) == []
    assert len(link(stages, [10_001])) == 1


def test_second_stage_must_come_after_the_first():
    # Order is the entire meaning of `A >> B`; a backwards pair is not a match.
    stages = [[hit("v1", 1, 10_000)], [hit("v1", 2, 1_000)]]
    assert link(stages, [30_000]) == []


def test_simultaneous_hits_do_not_chain():
    # Strictly greater than zero: a scene cannot follow something at its own timestamp.
    stages = [[hit("v1", 1, 5_000)], [hit("v1", 2, 5_000)]]
    assert link(stages, [30_000]) == []


def test_a_scene_cannot_follow_itself():
    # The same scene surfacing in both stages is one event, not a sequence.
    stages = [[hit("v1", 1, 1_000)], [hit("v1", 1, 1_000)]]
    assert link(stages, [30_000]) == []


def test_hits_in_different_videos_do_not_chain():
    stages = [[hit("v1", 1, 0)], [hit("v2", 2, 5_000)]]
    assert link(stages, [30_000]) == []


def test_video_answering_only_one_stage_is_dropped():
    stages = [
        [hit("v1", 1, 0), hit("v2", 3, 0)],
        [hit("v1", 2, 5_000)],
    ]
    chains = link(stages, [30_000])
    assert len(chains) == 1
    assert chains[0].scenes[0]["video_id"] == "v1"


# --- the dynamic programme ---------------------------------------------------------------------

def test_best_successor_wins_not_the_nearest():
    # The DP maximises the score reachable onward, so a better-scoring later hit beats a
    # weak nearby one. A greedy "first legal successor" implementation fails this.
    stages = [
        [hit("v1", 1, 0, score=1.0)],
        [hit("v1", 2, 1_000, score=0.1), hit("v1", 3, 2_000, score=0.9)],
    ]
    chains = link(stages, [30_000])
    assert len(chains) == 1
    assert chains[0].scenes[1]["scene_id"] == 3
    assert chains[0].score == 1.9


def test_three_stage_chain_composes():
    stages = [
        [hit("v1", 1, 0, score=1.0)],
        [hit("v1", 2, 5_000, score=1.0)],
        [hit("v1", 3, 9_000, score=1.0)],
    ]
    chains = link(stages, [10_000, 10_000])
    assert len(chains) == 1
    assert [s["scene_id"] for s in chains[0].scenes] == [1, 2, 3]
    assert chains[0].score == 3.0
    assert chains[0].gaps_ms == [5_000, 4_000]


def test_three_stage_chain_needs_every_window_satisfied():
    # The middle hit is reachable but nothing legal follows it, so no chain exists at all.
    stages = [
        [hit("v1", 1, 0)],
        [hit("v1", 2, 5_000)],
        [hit("v1", 3, 90_000)],
    ]
    assert link(stages, [10_000, 10_000]) == []


def test_middle_stage_is_chosen_so_the_whole_chain_survives():
    # Picking the higher-scoring middle hit would strand the chain, so the DP has to prefer the
    # one that can actually reach the end.
    stages = [
        [hit("v1", 1, 0, score=1.0)],
        [hit("v1", 2, 1_000, score=9.0), hit("v1", 3, 2_000, score=0.1)],
        [hit("v1", 4, 8_000, score=1.0)],
    ]
    chains = link(stages, [30_000, 7_000])
    assert len(chains) == 1
    assert [s["scene_id"] for s in chains[0].scenes] == [1, 3, 4]


# --- one chain per anchor, and the caps ---------------------------------------------------------

def test_one_chain_per_anchor():
    # Two anchors, both able to reach the same successor: two chains, not four.
    stages = [
        [hit("v1", 1, 0), hit("v1", 2, 1_000)],
        [hit("v1", 3, 5_000), hit("v1", 4, 6_000)],
    ]
    chains = link(stages, [30_000])
    assert len(chains) == 2
    assert sorted(c.scenes[0]["scene_id"] for c in chains) == [1, 2]


def test_max_per_video_caps_a_single_video():
    stages = [
        [hit("v1", 1, 0, score=0.1), hit("v1", 2, 1_000, score=0.9)],
        [hit("v1", 3, 5_000, score=1.0)],
    ]
    chains = link(stages, [30_000], max_per_video=1)
    assert len(chains) == 1
    # The cap keeps the best chain in that video, not an arbitrary one.
    assert chains[0].scenes[0]["scene_id"] == 2


def test_max_per_video_applies_per_video_not_globally():
    stages = [
        [hit("v1", 1, 0), hit("v1", 2, 1_000), hit("v2", 5, 0), hit("v2", 6, 1_000)],
        [hit("v1", 3, 5_000), hit("v2", 7, 5_000)],
    ]
    chains = link(stages, [30_000], max_per_video=1)
    assert len(chains) == 2
    assert sorted(c.scenes[0]["video_id"] for c in chains) == ["v1", "v2"]


def test_limit_truncates_the_final_ranking():
    stages = [
        [hit("v1", 1, 0, score=0.5), hit("v2", 3, 0, score=0.9)],
        [hit("v1", 2, 5_000, score=1.0), hit("v2", 4, 5_000, score=1.0)],
    ]
    chains = link(stages, [30_000], limit=1)
    assert len(chains) == 1
    assert chains[0].scenes[0]["video_id"] == "v2"


def test_chains_are_ranked_by_score():
    stages = [
        [hit("v1", 1, 0, score=0.1), hit("v2", 3, 0, score=5.0)],
        [hit("v1", 2, 5_000, score=1.0), hit("v2", 4, 5_000, score=1.0)],
    ]
    chains = link(stages, [30_000])
    assert [c.scenes[0]["video_id"] for c in chains] == ["v2", "v1"]


# --- timestamp fallback -------------------------------------------------------------------------

def test_start_ms_is_used_when_ts_ms_is_absent():
    stages = [
        [{"video_id": "v1", "scene_id": 1, "score": 1.0, "start_ms": 0}],
        [{"video_id": "v1", "scene_id": 2, "score": 1.0, "start_ms": 5_000}],
    ]
    chains = link(stages, [30_000])
    assert len(chains) == 1
    assert chains[0].gaps_ms == [5_000]
