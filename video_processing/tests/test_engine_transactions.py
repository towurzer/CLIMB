"""
The search engine's own transactions (retrieval/engine.py).

psycopg2 opens a transaction on the first statement, and an open transaction keeps its locks. The
worker builds the engine at startup; with a migration pending it refuses every search, so a
transaction left open by the build was never ended and `main.py migrate` waited on it.
"""

from contextlib import contextmanager

from retrieval import engine


class _Connection:
    """Answers every query with nothing, so no model is registered and none loads."""

    def __init__(self):
        self.rollbacks = 0

    @contextmanager
    def cursor(self):
        class Cursor:
            def execute(self, *args):
                pass

            def fetchone(self):
                return None

            def fetchall(self):
                return []

        yield Cursor()

    def rollback(self):
        self.rollbacks += 1


def test_building_the_engine_ends_the_transaction_its_loading_opened(monkeypatch):
    monkeypatch.setattr(engine, "vector_counts", lambda conn: ({}, 0))
    conn = _Connection()
    built = engine.SearchEngine(conn)
    assert conn.rollbacks == 1
    assert not built.ready
