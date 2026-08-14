"""
Reciprocal rank fusion.

By rank, not by score, because the retrievers are not on comparable scales: SigLIP2 cosine sits
around 0.03-0.12, e5 cosine around 0.75-0.90, and ts_rank_cd is unbounded. Normalising those
against each other needs per-retriever calibration that would have to be re-tuned whenever a model
changes. RRF needs none, it only asks "where did this retriever put it".

    score(keyframe) = sum over retrievers of  weight / (K + rank)
"""

from collections import defaultdict
from dataclasses import dataclass, field

RRF_K = 60  # standard; large enough that the top few ranks are not wildly separated


@dataclass
class FusedResult:
    keyframe_id: int
    scene_id: int
    score: float
    # Which retriever contributed and at what rank; shown in the UI and used by the eval
    # harness. Without it a fused ranking is unexplainable.
    signals: dict = field(default_factory=dict)
    # The same contributions as `signals`, but in score rather than rank: retriever -> its share of
    # `score`. Rank alone cannot say who is responsible for a hit, because the weights differ;
    # OCR at rank 20 (4/80) outweighs the visual retriever at rank 5 (1/65). The UI orders the
    # per-result signal badges by this, and only the fusion knows the weights.
    contributions: dict = field(default_factory=dict)


def fuse(ranked_lists: dict, weights: dict, k: int = RRF_K, limit: int = None) -> list:
    """
    `ranked_lists` maps retriever name -> [(scene_id, keyframe_id)] in rank order.
    Returns FusedResult, best first.

    Entries with no keyframe are dropped: every retriever now resolves to keyframes before it
    returns (the scene-level ones expand across the scene's keyframes), so a None here means the
    scene had no keyframe rows at all, which is not something to rank.
    """
    scores = defaultdict(float)
    signals = defaultdict(dict)
    contributions = defaultdict(dict)
    scene_of = {}

    for name, results in ranked_lists.items():
        weight = weights.get(name, 1.0)
        if weight <= 0:
            continue
        for rank, entry in enumerate(results, start=1):
            scene_id, keyframe_id = entry if isinstance(entry, tuple) else (entry, None)
            if keyframe_id is None:
                continue
            contribution = weight / (k + rank)
            scores[keyframe_id] += contribution
            scene_of[keyframe_id] = scene_id
            previous = signals[keyframe_id].get(name)
            signals[keyframe_id][name] = rank if previous is None else min(previous, rank)
            contributions[keyframe_id][name] = (
                contributions[keyframe_id].get(name, 0.0) + contribution
            )

    fused = [FusedResult(keyframe_id=keyframe_id, scene_id=scene_of[keyframe_id], score=score,
                         signals=dict(signals[keyframe_id]),
                         contributions=dict(contributions[keyframe_id]))
             for keyframe_id, score in scores.items()]
    fused.sort(key=lambda r: (-r.score, r.keyframe_id))
    return fused[:limit] if limit else fused
