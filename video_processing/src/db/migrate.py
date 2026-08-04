"""
Forward-only SQL migration runner.
"""

import hashlib
import re
from pathlib import Path

import custom_logger
from config import Config

MIGRATION_FILENAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

CREATE_MIGRATIONS_TABLE = """
                          CREATE TABLE IF NOT EXISTS schema_migrations
                          (
                              version     TEXT PRIMARY KEY,
                              name        TEXT        NOT NULL,
                              checksum    TEXT        NOT NULL,
                              applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                          );
                          """


def discover_migrations():
    """Returns [(version, name, path)] sorted by version, rejecting misnamed files."""
    conf = Config()
    migrations_dir = Path(conf.MIGRATIONS_DIR)
    logger = custom_logger.get_logger("migrate")

    if not migrations_dir.is_dir():
        logger.error(f"Migrations directory not found: {migrations_dir}")
        return []

    found = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_FILENAME_RE.match(path.name)
        if not match:
            # Loud rather than skipped: a typo'd filename silently not running is exactly
            # the failure this runner exists to prevent.
            raise ValueError(
                f"Migration file '{path.name}' does not match NNN_description.sql"
            )
        found.append((match.group(1), path.stem, path))

    versions = [v for v, _, _ in found]
    duplicates = {v for v in versions if versions.count(v) > 1}
    if duplicates:
        raise ValueError(f"Duplicate migration version(s): {sorted(duplicates)}")

    return found


def applied_migrations(conn):
    """Returns {version: checksum} for everything already applied."""
    with conn.cursor() as cur:
        cur.execute(CREATE_MIGRATIONS_TABLE)
        conn.commit()
        cur.execute("SELECT version, checksum FROM schema_migrations;")
        return dict(cur.fetchall())


def run_migrations(conn):
    """
    Applies every pending migration in order. Returns the number applied.

    Verifies the checksum of already-applied files: an edited migration means this database
    and another have silently diverged, which is worth failing over rather than ignoring.
    """
    logger = custom_logger.get_logger("migrate")

    migrations = discover_migrations()
    if not migrations:
        logger.warning("No migration files found.")
        return 0

    already = applied_migrations(conn)
    applied_count = 0

    for version, name, path in migrations:
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

        if version in already:
            if already[version] != checksum:
                raise RuntimeError(
                    f"Migration {version} ({name}) has changed since it was applied. "
                    f"Migrations are immutable once applied -- add a new one instead."
                )
            logger.debug(f"Migration {version} already applied, skipping.")
            continue

        logger.info(f"Applying migration {version}: {name}")
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s);",
                    (version, name, checksum),
                )
            conn.commit()
            applied_count += 1
        except Exception as e:
            conn.rollback()
            logger.error(f"Migration {version} ({name}) failed, rolled back: {e}")
            raise

    if applied_count == 0:
        logger.info("Database schema is up to date.")
    else:
        logger.info(f"Applied {applied_count} migration(s).")

    return applied_count


def migration_status(conn):
    """Returns [(version, name, applied_bool)] for reporting."""
    already = applied_migrations(conn)
    return [(v, n, v in already) for v, n, _ in discover_migrations()]
