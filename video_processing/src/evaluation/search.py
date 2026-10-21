"""
A search over precomputed frame vectors, ranked and fused the way the engine does it: each model returns
its CLIMB_RETRIEVER_DEPTH best frames and retrieval.fusion fuses those lists. Frames are indices into the
vector arrays and stand in for keyframe ids. Also the average precision of a ranking.
"""

import numpy as np

from retrieval import fusion

LABELS = {"visual": "SigLIP2", "peskavlp": "PeskaVLP"}


def ranked(similarities, depth=None) -> np.ndarray:
    """Frame indices, most similar first, ties on the index; the first `depth` of them (None: all)."""
    order = np.lexsort((np.arange(len(similarities)), -np.asarray(similarities)))
    return order if depth is None else order[:depth]


def fused(lists: dict, weights: dict) -> list:
    """The frame indices retrieval.fusion returns for {signal: ranked indices}, best first."""
    results = fusion.fuse({name: [(int(i), int(i)) for i in order] for name, order in lists.items()},
                          weights)
    return [result.keyframe_id for result in results]


def average_precision(order, relevant) -> float:
    """Over the ranked frame indices `order`; relevant frames it does not hold count as never found."""
    hits, total = 0, 0.0
    for rank, index in enumerate(order, start=1):
        if relevant[index]:
            hits += 1
            total += hits / rank
    return total / max(1, int(relevant.sum()))


def variants(similarities: dict, weights, depth=None) -> dict:
    """
    {variant: frame indices} for {signal: similarities} of SigLIP2 ("visual") and, if there is one, a
    collection's model: each alone, then both fused with the collection's model at each of `weights`.
    Every model's list is cut at `depth` first.
    """
    orders = {signal: ranked(s, depth) for signal, s in similarities.items()}
    found = {LABELS.get(signal, signal): order for signal, order in orders.items()}
    domain = next((signal for signal in orders if signal != "visual"), None)
    for w in weights if domain else ():
        found[f"both, {w}"] = fused(orders, {"visual": 1.0, domain: w})
    return found
