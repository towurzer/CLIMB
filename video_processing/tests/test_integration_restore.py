"""
A dump has to restore: that is how everyone but the machine that ran the pipeline gets a database.

pg_restore runs with an empty search_path, so a SQL function whose body calls another of ours
without `public.` restores as written and then fails the first time something evaluates it, such
as a generated column in CREATE TABLE (migration 016). Postgres' own validator for SQL functions,
run under the same empty search_path, reports that without making a dump.
"""

import psycopg2
import pytest

pytestmark = pytest.mark.integration

OUR_SQL_FUNCTIONS = """
    SELECT p.oid, p.oid::regprocedure::text
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    JOIN pg_language l ON l.oid = p.prolang
    WHERE n.nspname = 'public'
      AND l.lanname = 'sql'
      AND NOT EXISTS (SELECT 1 FROM pg_depend d
                      WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
    ORDER BY 2
"""


def test_sql_functions_resolve_with_an_empty_search_path(db):
    with db.cursor() as cur:
        cur.execute(OUR_SQL_FUNCTIONS)
        functions = cur.fetchall()
    db.rollback()
    assert functions, "no SQL functions of ours in the database; have the migrations run?"

    failures = {}
    for oid, name in functions:
        try:
            with db.cursor() as cur:
                cur.execute("SET LOCAL search_path = ''")
                cur.execute("SELECT pg_catalog.fmgr_sql_validator(%s::oid)", (oid,))
        except psycopg2.Error as error:
            failures[name] = str(error).splitlines()[0]
        finally:
            db.rollback()
    assert not failures, failures
