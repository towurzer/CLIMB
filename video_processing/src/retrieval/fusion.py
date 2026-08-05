"""
Reciprocal rank fusion.

By rank, not by score, because the retrievers are not on comparable scales: SigLIP2 cosine sits
around 0.03-0.12, e5 cosine around 0.75-0.90, and ts_rank_cd is unbounded. Normalising those
against each other needs per-retriever calibration that would have to be re-tuned whenever a model
changes. RRF needs none -- it only asks "where did this retriever put it".

    score(scene) = sum over retrievers of  weight / (K + rank)
"""

from collections import defaultdict
from dataclasses import dataclass, field

RRF_K = 60  # standard; large enough that the top few ranks are not wildly separated


@dataclass
class FusedResult:
    scene_id: int
    score: float
    # Which retriever contributed and at what rank -- shown in the UI and used by the eval
    # harness. Without it a fused ranking is unexplainable.
    signals: dict = field(default_factory=dict)
    keyframe_id: int | None = None


def fuse(ranked_lists: dict, weights: dict, k: int = RRF_K, limit: int = None) -> list:
    """
    `ranked_lists` maps retriever name -> [(scene_id, keyframe_id_or_None)] in rank order.
    Returns FusedResult, best first.
    """
    scores = defaultdict(float)
    signals = defaultdict(dict)
    keyframes = {}

    for name, results in ranked_lists.items():
        weight = weights.get(name, 1.0)
        if weight <= 0:
            continue
        for rank, entry in enumerate(results, start=1):
            scene_id, keyframe_id = entry if isinstance(entry, tuple) else (entry, None)
            scores[scene_id] += weight / (k + rank)
            signals[scene_id][name] = rank
            # First retriever to name a keyframe for this scene decides what gets shown. Retriever
            # order therefore matters: the visual one runs first because its keyframe is the one
            # that actually matched, where OCR or ASR only identify the scene.
            if keyframe_id is not None and scene_id not in keyframes:
                keyframes[scene_id] = keyframe_id

    fused = [FusedResult(scene_id=scene_id, score=score, signals=dict(signals[scene_id]),
                         keyframe_id=keyframes.get(scene_id))
             for scene_id, score in scores.items()]
    fused.sort(key=lambda r: (-r.score, r.scene_id))
    return fused[:limit] if limit else fused
