"""
Retrievers running side by side on pooled connections. No database: fake connections record what
was done to them.
"""

import threading

import pytest

from db.pool import ConnectionPool
from retrieval.engine import SearchEngine


class FakeConn:
    def __init__(self):
        self.closed = False
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def test_a_returned_connection_is_rolled_back_and_reused():
    made = []
    pool = ConnectionPool(1, connect=lambda: made.append(FakeConn()) or made[-1])
    with pool.connection() as first:
        pass
    with pool.connection() as second:
        pass
    assert first is second and len(made) == 1
    assert first.rollbacks == 2  # once per return: a borrower's transaction never outlives it


def test_a_dead_connection_is_replaced():
    made = []
    pool = ConnectionPool(1, connect=lambda: made.append(FakeConn()) or made[-1])
    with pool.connection() as conn:
        conn.closed = True
    with pool.connection() as conn:
        assert not conn.closed
    assert len(made) == 2


def test_a_failed_connect_does_not_lose_the_slot():
    attempts = []

    def connect():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("database not reachable")
        return FakeConn()

    pool = ConnectionPool(1, connect=connect)
    with pytest.raises(ConnectionError):
        with pool.connection():
            pass
    with pool.connection() as conn:  # would block forever if the slot had been lost
        assert isinstance(conn, FakeConn)


def test_two_borrowers_never_share_a_connection():
    pool = ConnectionPool(2, connect=FakeConn)
    held, release = [], threading.Event()

    def borrow():
        with pool.connection() as conn:
            held.append(conn)
            release.wait(5)

    threads = [threading.Thread(target=borrow) for _ in range(2)]
    for t in threads:
        t.start()
    while len(held) < 2:
        pass
    release.set()
    for t in threads:
        t.join()
    assert held[0] is not held[1]


def _engine(pool_size):
    from concurrent.futures import ThreadPoolExecutor

    import custom_logger
    instance = SearchEngine.__new__(SearchEngine)  # no models, no database
    instance.conn = "main"
    instance.logger = custom_logger.get_logger("test")
    instance.pool = ConnectionPool(pool_size, connect=FakeConn) if pool_size > 1 else None
    instance._retriever_threads = ThreadPoolExecutor(pool_size) if pool_size > 1 else None
    return instance


def test_retrievers_run_on_pooled_connections_and_come_back_in_queue_order():
    engine = _engine(3)
    started = threading.Barrier(3, timeout=5)  # all three must be running at the same time

    def retriever(value):
        def fn(conn):
            assert isinstance(conn, FakeConn)
            started.wait()
            return [value]
        return fn

    out = engine._run_retrievers([("visual", retriever(1)), ("caption", retriever(2)),
                                  ("transcript", retriever(3))])
    assert [(name, ranked) for name, ranked, _ in out] == [
        ("visual", [1]), ("caption", [2]), ("transcript", [3])]


def test_a_failing_retriever_yields_an_empty_list_and_the_rest_still_run():
    engine = _engine(2)

    def broken(conn):
        raise RuntimeError("boom")

    out = engine._run_retrievers([("ocr", broken), ("visual", lambda conn: [7])])
    assert [(name, ranked) for name, ranked, _ in out] == [("ocr", []), ("visual", [7])]


def test_without_a_pool_they_run_on_the_engines_connection():
    engine = _engine(1)
    out = engine._run_retrievers([("visual", lambda conn: [conn]), ("ocr", lambda conn: [conn])])
    assert [ranked for _, ranked, _ in out] == [["main"], ["main"]]
