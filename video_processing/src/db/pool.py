"""
A fixed set of extra connections, so the retrievers of one search can run side by side.

psycopg2 runs one statement at a time per connection, and a transaction belongs to its connection.
So every retriever running in parallel needs a connection of its own, and two requests must never
share one: the first to roll back would end the other's transaction, and with it the SET LOCAL
hnsw settings its ANN query depends on, with no error anywhere.
"""

import queue
from contextlib import contextmanager

from db.connection import connect_to_database
from db.index_ops import apply_serve_tuning


def connect_tuned():
    """A new connection with the serve tuning applied (and committed: a rollback would undo a SET)."""
    conn = connect_to_database()
    if conn is None:
        raise ConnectionError("database not reachable")
    apply_serve_tuning(conn)
    conn.commit()
    return conn


class ConnectionPool:
    """
    `size` connections, opened on first use. `connection()` blocks while all of them are lent out,
    rolls back what the borrower left open, and replaces a connection that died.
    """

    def __init__(self, size, connect=connect_tuned):
        self.size = size
        self._connect = connect
        self._free = queue.LifoQueue()
        for _ in range(size):
            self._free.put(None)

    @contextmanager
    def connection(self):
        conn = self._free.get()
        try:
            if conn is None or conn.closed:
                conn = self._connect()
            yield conn
        finally:
            if conn is not None and not conn.closed:
                try:
                    conn.rollback()
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._free.put(conn if conn is not None and not conn.closed else None)
