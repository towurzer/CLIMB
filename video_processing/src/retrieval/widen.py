"""
The background search that grows a sequence's results while they are on screen.

A chain needs a hit from every stage in one video, and a stage's hits stop at what the ANN index
hands over: `video game sunset >> door >> king` found nothing on full V3C, with 00128's `door`
frames at exact rank 7016 and the live path's candidates stopping at 4000. So once the first
results are out, the videos the stages point at (their candidates) are ranked by exact distance,
and the chains found there are appended, a rung of the ladder at a time. Nothing already shown
moves, so nobody clicks the wrong card. Measured in EXPERIMENTS.md, 2026-11-05.

The parts that need neither a database nor a model live here; SearchEngine.widen runs them.
"""

import itertools
import threading
from collections import Counter, OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass

# Clients whose latest turn is remembered. Each tab is one client.
TURN_CLIENTS = 64


def depth_ladder(first, step, maximum) -> list:
    """
    The depths a sequence widens through: `first`, then the multiples of `step` above its last one,
    none above `maximum`. (4000, 8000), 5000 and 32000 give 4000, 8000, 10000, 15000 ... 30000.
    """
    depths = [depth for depth in first if depth <= maximum]
    if len(depths) == len(first) and first and step > 0:
        depths += range((first[-1] // step + 1) * step, maximum + 1, step)
    return depths


def next_depth(ladder, depth):
    """The rung after `depth`, or None at the top."""
    return next((rung for rung in ladder if rung > depth), None)


def round_robin(lists, size) -> list:
    """The first `size` distinct items taking one from each list in turn, so no list can crowd out
    the others."""
    picked = {}
    for column in itertools.zip_longest(*lists):
        for item in column:
            if item is not None and item not in picked:
                picked[item] = None
                if len(picked) == size:
                    return list(picked)
    return list(picked)


def unseen_chains(chains, seen, max_per_video) -> list:
    """
    The chains worth appending to what is shown already, given as (recording id, anchor keyframe).

    An anchor gets one card, and TEMPORAL_MAX_PER_VIDEO holds across rungs: each rung alone would
    keep 3 per video, and 7 rungs of them would let a long video own the end of the list. A video
    already at the cap has been found, so this costs no video. It does cost chains inside it.
    """
    anchors = {anchor for _, anchor in seen}
    per_video = Counter(video for video, _ in seen)
    fresh = []
    for chain in chains:
        anchor, video = chain.scenes[0]["keyframe_id"], chain.scenes[0]["video_id"]
        if anchor in anchors or per_video[video] >= max_per_video:
            continue
        anchors.add(anchor)
        per_video[video] += 1
        fresh.append(chain)
    return fresh


class Superseded(Exception):
    """A newer request of the same client took over, so this one stops where it is."""


class Foreground:
    """
    The searches an operator is waiting for. The background search waits for there to be none.

    A new sequence took 4-6.5 s beside it, and 2.4-3.2 s without it. Its Python and its scans compete
    with the foreground's, and a thread that keeps the interpreter busy delays the others' every step.
    """

    def __init__(self):
        self._running = 0
        self._changed = threading.Condition()

    @contextmanager
    def running(self):
        with self._changed:
            self._running += 1
        try:
            yield
        finally:
            with self._changed:
                self._running -= 1
                self._changed.notify_all()

    def wait_until_idle(self):
        with self._changed:
            while self._running:
                self._changed.wait()


class Turns:
    """A client asking again makes its earlier request stale. How a new query stops the old one."""

    def __init__(self, clients=TURN_CLIENTS):
        self._clients = clients
        self._latest = OrderedDict()
        self._lock = threading.Lock()

    def take(self, client):
        """Registers a request and returns a function telling whether it is still the latest."""
        turn = object()
        with self._lock:
            self._latest[client] = turn
            self._latest.move_to_end(client)
            while len(self._latest) > self._clients:
                self._latest.popitem(last=False)
        return lambda: self._latest.get(client) is turn


@dataclass
class StageLists:
    """One stage's ranked lists at the ladder's full depth, and the weights they fuse with."""
    lists: dict
    weights: dict


@dataclass
class Ladder:
    """What rungs of one search share: each is a prefix of these lists, up to `depth`."""
    key: tuple
    depth: int
    # The videos the lists rank inside, so a deeper pass does not have to pick them again.
    candidates: list
    stages: list
    # keyframe id -> its video, for the video-level signals
    video_of: dict
    # keyframe id -> (recording id, ms on the recording's clock), for linking
    link_meta: dict
