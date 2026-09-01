"""Reconcile a live Postgres schema with the declarations in `schema.py`.

`metadata.create_all(engine, checkfirst=True)` (used at every store startup)
only creates tables that don't exist yet -- it silently no-ops on a table
that already exists, even when that table's declaration in `schema.py` has
since grown a new column. That gap caused a real production incident: the
multi-project feature added `project_id` to six tables' declarations here,
but the already-deployed hosted database already had those tables from
before that change, so `create_all` never touched them. Every project-scoped
query against those tables then failed in production with
`psycopg.errors.UndefinedColumn: column artifacts.project_id does not
exist` -- `create_all` had nothing to say about it, and nothing else in the
startup path checked.

`ensure_schema` closes that gap: after `create_all`, it walks every declared
table that already existed and adds any column present in the declaration
but missing from the live table (additive only -- it never drops or alters
an existing column, so it can't destroy data). It also widens a table's
primary key to match the declared one when a newly-added column extends it
(exactly what happened to `artifacts` and `dependency_graph` above). Safe to
call on every process start, including against a brand new database where
every table was just created fresh by `create_all` (nothing to reconcile).
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from .schema import metadata


def ensure_schema(engine: Engine) -> None:
    # `existing_tables` only needs to see tables committed before this call
    # (create_all's own transaction already committed by the time we get
    # here), so an engine-bound inspector is fine for that one check. Every
    # inspection *inside* the transaction below is bound to `conn` instead --
    # an engine-bound inspector opens its own connection per call and, under
    # Postgres's default READ COMMITTED isolation, would not see this
    # transaction's own not-yet-committed ADD COLUMN/DROP CONSTRAINT work.
    existing_tables = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all already created this one from scratch
            # A fresh Inspector per reflection call, deliberately not reused
            # across the ALTER statements below: SQLAlchemy's Inspector
            # caches get_columns/get_pk_constraint results per-instance, so
            # reusing one instance made every check here see the table's
            # pre-ALTER shape even after later DDL in this same transaction
            # -- silently no-op'ing the primary-key widening below (caught
            # by test_db_migrate.py, which asserts against the live PK after
            # this runs, not just "no exception was raised").
            live_columns = inspect(conn).get_columns(table.name)
            live_column_names = {col["name"] for col in live_columns}
            declared_column_names = {col.name for col in table.columns}
            added_any = False
            for column in table.columns:
                if column.name in live_column_names:
                    continue
                _add_column(conn, table.name, column)
                added_any = True
            if added_any:
                _reconcile_primary_key(conn, inspect(conn), table)
            # A live column no longer declared here (e.g. `dependency_graph.id`,
            # the old primary key before it was replaced by `project_id`) is
            # left in place -- dropping columns automatically is not something
            # this reconciliation does, on the chance the data still matters.
            # But if it's still NOT NULL, any future insert through the
            # declared table (which never sets that column) would violate that
            # constraint, so it's relaxed to nullable. Must be re-reflected
            # after the primary-key reconciliation above: a column that was
            # NOT NULL only by virtue of being *part of* the old primary key
            # (e.g. dependency_graph.id) can't have NOT NULL dropped while
            # it's still a PK column -- Postgres rejects that outright -- so
            # this has to run after that PK has already been replaced.
            for column in inspect(conn).get_columns(table.name):
                if column["name"] in declared_column_names or column["nullable"]:
                    continue
                conn.execute(text(f'ALTER TABLE "{table.name}" ALTER COLUMN "{column["name"]}" DROP NOT NULL'))


def _add_column(conn: Connection, table_name: str, column) -> None:
    ddl_type = column.type.compile(dialect=conn.dialect)
    # `project_id` is the one column this migration actually exists to add
    # (see module docstring) -- backfill it to the single-project default so
    # existing rows, written before multi-project support, land in the same
    # project the filesystem-backed store would have put them in. Any other
    # newly-declared NOT NULL column is added nullable instead of guessing a
    # default value for a case we don't yet have.
    if not column.nullable and column.name == "project_id":
        conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {ddl_type} NOT NULL DEFAULT \'default\''))
    else:
        conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {ddl_type}'))


def _reconcile_primary_key(conn: Connection, inspector, table) -> None:
    declared_pk = [col.name for col in table.primary_key.columns]
    if not declared_pk:
        return
    live_pk = inspector.get_pk_constraint(table.name)
    live_pk_columns = live_pk.get("constrained_columns") or []
    if set(live_pk_columns) == set(declared_pk):
        return
    live_columns = {col["name"] for col in inspector.get_columns(table.name)}
    if not set(declared_pk) <= live_columns:
        return  # a declared PK column isn't actually present yet -- leave the live PK alone
    constraint_name = live_pk.get("name")
    if constraint_name:
        conn.execute(text(f'ALTER TABLE "{table.name}" DROP CONSTRAINT "{constraint_name}"'))
    quoted_cols = ", ".join(f'"{name}"' for name in declared_pk)
    conn.execute(text(f'ALTER TABLE "{table.name}" ADD PRIMARY KEY ({quoted_cols})'))
