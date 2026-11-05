"""
The parts of the background search that need no database: the rungs, the candidates, what gets
appended, and a new query stopping the old one. The search itself: test_integration_api.py.
"""

import threading
import time

import pytest

from retrieval import widen
from retrieval.engine import SearchEngine
from retrieval.temporal import Chain


def test_the_ladder_steps_up_to_the_limit_and_stops_below_it():
    assert widen.depth_ladder((4000, 8000), 5000, 32000) == [
        4000, 8000, 10000, 15000, 20000, 25000, 30000]


def test_a_limit_on_a_rung_includes_it():
    assert widen.depth_ladder((4000, 8000), 5000, 30000)[-1] == 30000


@pytest.mark.parametrize("maximum,expected", [
    (0, []), (3999, []), (4000, [4000]), (9999, [4000, 8000]), (10000, [4000, 8000, 10000])])
def test_a_low_limit_cuts_the_first_depths_too(maximum, expected):
    assert widen.depth_ladder((4000, 8000), 5000, maximum) == expected


def test_each_rung_names_the_next_and_the_top_names_none():
    ladder = [4000, 8000, 10000]
    assert widen.next_depth(ladder, 4000) == 8000
    assert widen.next_depth(ladder, 10000) is None
    assert widen.next_depth(ladder, 9000) == 10000  # a depth from before the limit changed


def test_round_robin_gives_each_list_a_turn_before_cutting():
    assert widen.round_robin([["a", "b", "c"], ["x", "a", "y"]], 4) == ["a", "x", "b", "c"]


def test_round_robin_survives_lists_of_different_lengths_and_none_at_all():
    assert widen.round_robin([["a"], [], ["b", "c"]], 10) == ["a", "b", "c"]
    assert widen.round_robin([], 10) == []


def chain(video, anchor):
    return Chain(scenes=[{"keyframe_id": anchor, "video_id": video}])


def test_a_shown_anchor_gets_no_second_card():
    fresh = widen.unseen_chains([chain("v1", 1), chain("v1", 2)], [("v1", 1)], 3)
    assert [c.scenes[0]["keyframe_id"] for c in fresh] == [2]


def test_two_chains_in_one_rung_cannot_share_an_anchor():
    fresh = widen.unseen_chains([chain("v1", 1), chain("v1", 1)], [], 3)
    assert len(fresh) == 1


def test_the_per_video_cap_counts_what_is_shown_already():
    seen = [("v1", 1), ("v1", 2)]
    fresh = widen.unseen_chains([chain("v1", 3), chain("v1", 4), chain("v2", 5)], seen, 3)
    assert [c.scenes[0]["keyframe_id"] for c in fresh] == [3, 5]


def test_the_order_of_a_rung_is_kept():
    fresh = widen.unseen_chains([chain("v2", 9), chain("v1", 3), chain("v3", 5)], [], 3)
    assert [c.scenes[0]["keyframe_id"] for c in fresh] == [9, 3, 5]


def test_a_new_turn_makes_the_old_one_stale():
    turns = widen.Turns()
    first = turns.take("tab")
    assert first()
    second = turns.take("tab")
    assert not first() and second()


def test_clients_do_not_stop_each_other():
    turns = widen.Turns()
    mine, yours = turns.take("mine"), turns.take("yours")
    assert mine() and yours()


def test_forgotten_clients_are_stale_and_the_table_stays_small():
    turns = widen.Turns(clients=2)
    oldest = turns.take("a")
    turns.take("b")
    turns.take("c")
    assert not oldest()
    assert len(turns._latest) == 2


def test_turns_are_safe_to_take_from_many_threads():
    turns = widen.Turns()
    latest = []

    def ask():
        latest.append(turns.take("tab"))

    threads = [threading.Thread(target=ask) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(is_latest() for is_latest in latest) == 1


def _engine(rung):
    instance = SearchEngine.__new__(SearchEngine)  # no models, no database
    instance._widen_turns = widen.Turns()
    instance._widen_lock = threading.Lock()
    instance._foreground_requests = widen.Foreground()
    instance._widen_rung = rung
    return instance


def test_a_rung_waiting_behind_another_is_dropped_when_a_new_query_arrives():
    running, release, ran = threading.Event(), threading.Event(), []

    def rung(query, *args):
        ran.append(query)
        running.set()
        release.wait(5)
        return query

    engine = _engine(rung)
    answers = {}

    def ask(query):
        answers[query] = engine.widen(query, 4000, client="tab")

    first = threading.Thread(target=ask, args=("first",))
    first.start()
    assert running.wait(5)
    # first is running; second queues behind it, and third makes second stale while it waits
    second = threading.Thread(target=ask, args=("second",))
    second.start()
    third = threading.Thread(target=ask, args=("third",))
    # Second must have taken its turn before third does, or there is nothing to test.
    time.sleep(0.2)
    third.start()
    time.sleep(0.2)
    release.set()
    for thread in (first, second, third):
        thread.join(5)
    assert ran == ["first", "third"]
    assert answers["second"].cancelled


def test_a_rung_waits_for_the_foreground_at_its_checkpoints():
    reached, finished = threading.Event(), threading.Event()

    def rung(query, depth, exclude, collection, sources, seen, client, checkpoint):
        reached.set()
        checkpoint()
        finished.set()

    engine = _engine(rung)
    with engine._foreground_requests.running():
        worker = threading.Thread(target=engine.widen, args=("q", 4000))
        worker.start()
        # The first checkpoint is before the rung, so it has not even started.
        assert not reached.wait(0.2)
    worker.join(5)
    assert reached.is_set() and finished.is_set()


def test_a_rung_stops_at_a_checkpoint_once_a_new_query_arrived():
    entered, carry_on = threading.Event(), threading.Event()
    got_past = []

    def rung(query, depth, exclude, collection, sources, seen, client, checkpoint):
        if query == "old":
            entered.set()
            carry_on.wait(5)
            checkpoint()
            got_past.append(query)

    engine = _engine(rung)
    answers = {}
    old = threading.Thread(target=lambda: answers.update(old=engine.widen("old", 4000, client="tab")))
    old.start()
    assert entered.wait(5)
    new = threading.Thread(target=lambda: answers.update(new=engine.widen("new", 4000, client="tab")))
    new.start()
    time.sleep(0.2)  # the new one has taken its turn and queues behind the old one
    carry_on.set()
    old.join(5)
    new.join(5)
    assert answers["old"].cancelled and got_past == []


def test_another_clients_rung_is_not_dropped():
    ran = []
    engine = _engine(lambda query, *args: ran.append(query))
    engine.widen("mine", 4000, client="a")
    engine.widen("yours", 4000, client="b")
    assert ran == ["mine", "yours"]


def test_small_scopes_stay_the_only_exact_ones_unless_told_otherwise(monkeypatch):
    from retrieval import retrievers
    monkeypatch.setattr(retrievers.Config, "EXACT_SCOPE_MAX_VIDEOS", 2)
    assert not retrievers.Scope(only=("a", "b", "c")).exact
    assert retrievers.Scope(only=("a", "b", "c"), always_exact=True).exact
    # Nothing to name in the exact query: the index it is.
    assert not retrievers.Scope(always_exact=True).exact
