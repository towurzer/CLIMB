from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Chain:
    # One enriched result dict per stage, in query order. scenes[0] is the anchor.
    scenes: list = field(default_factory=list)
    score: float = 0.0
    # Gaps actually measured between consecutive stages, not the windows they had to fit in.
    gaps_ms: list = field(default_factory=list)


def link(stages, gaps_ms, limit=None, max_per_video=None) -> list:
    """
    `stages` is one ranked list of enriched hits per stage, `gaps_ms` one window per gap.
    Returns Chains, best first.
    """
    if len(stages) < 2 or len(gaps_ms) != len(stages) - 1:
        return []

    by_video = defaultdict(lambda: [[] for _ in stages])
    for index, hits in enumerate(stages):
        for hit in hits:
            by_video[hit["video_id"]][index].append(hit)

    chains = []
    for video_hits in by_video.values():
        # A chain needs every stage. Most candidate videos answer only one of them, and dropping
        # those here is what keeps the quadratic step below small.
        if any(not hits for hits in video_hits):
            continue
        for hits in video_hits:
            hits.sort(key=_time_of)

        ranked = sorted(_chains_in_video(video_hits, gaps_ms), key=lambda c: -c.score)
        chains.extend(ranked[:max_per_video] if max_per_video else ranked)

    chains.sort(key=lambda c: (-c.score, c.scenes[0]["scene_id"]))
    return chains[:limit] if limit else chains


def _chains_in_video(stage_hits, gaps_ms) -> list:
    """
    The best chain *starting* at each hit of the first stage, by dynamic programming.

    best[j][a] = score(a) + max{ best[j+1][b] : b is a legal successor of a }

    The inner search is a plain nested loop rather than a two-pointer prefix maximum: it is
    O(n_j * n_j+1) within one video, bounded by the stage depth even when a single video answers
    the whole query, and it stays readable, which the two-pointer version does not.
    """
    last = len(stage_hits) - 1
    best = [None] * len(stage_hits)
    best[last] = [(hit["score"], None) for hit in stage_hits[last]]

    for stage in range(last - 1, -1, -1):
        window = gaps_ms[stage]
        row = []
        for hit in stage_hits[stage]:
            time = _time_of(hit)
            carried, forward = None, None
            for index, following in enumerate(stage_hits[stage + 1]):
                reached = best[stage + 1][index][0]
                # None means no chain continues from there, so it cannot extend this one either.
                if reached is None or following["scene_id"] == hit["scene_id"]:
                    continue
                if not 0 < _time_of(following) - time < window:
                    continue
                if carried is None or reached > carried:
                    carried, forward = reached, index
            row.append((None if carried is None else carried + hit["score"], forward))
        best[stage] = row

    chains = []
    for index, (score, forward) in enumerate(best[0]):
        if score is None:
            continue
        path, stage, cursor = [index], 0, forward
        while stage < last:
            path.append(cursor)
            stage += 1
            cursor = best[stage][cursor][1]

        scenes = [stage_hits[i][position] for i, position in enumerate(path)]
        chains.append(Chain(
            scenes=scenes,
            score=score,
            gaps_ms=[_time_of(b) - _time_of(a) for a, b in zip(scenes, scenes[1:])],
        ))
    return chains


def _time_of(hit) -> int:
    """The matched keyframe's timestamp. Enrichment always attaches one; the fallback is paranoia."""
    timestamp = hit.get("ts_ms")
    return timestamp if timestamp is not None else (hit.get("start_ms") or 0)
