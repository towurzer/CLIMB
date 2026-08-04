import contextlib

import psycopg2

import custom_logger
from config import Config


def connect_to_database():
    """
    Opens a psycopg2 connection from the .env configuration.

    Returns None on failure rather than raising, because several callers treat an unavailable
    database as a degraded-but-serving state rather than a crash.
    """
    conf = Config()
    logger = custom_logger.get_logger("db_connect")
    try:
        logger.debug("Connecting to PostgreSQL...")
        return psycopg2.connect(
            dbname=conf.db_name,
            user=conf.db_user,
            password=conf.db_password,
            host=conf.db_host,
            port=conf.db_port,
        )
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return None


@contextlib.contextmanager
def connection_scope():
    """
    Connection context manager that commits on success and rolls back on any exception.

    The pipeline stages are long-running and partially resumable, so a stage that dies
    halfway must not leave a half-applied batch behind for the next worker to trip over.

    Raises ConnectionError if the database is unreachable -- unlike connect_to_database(),
    batch stages have nothing useful to do without a database.
    """
    conn = connect_to_database()
    if conn is None:
        raise ConnectionError("Could not connect to PostgreSQL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
