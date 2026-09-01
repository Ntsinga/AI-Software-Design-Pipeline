"""Exercises `db.migrate.ensure_schema` against a real database.

Skipped by default -- same requirements as test_postgres_store.py: the
`postgres` extra installed and `TEST_DATABASE_URL` set to a throwaway
Postgres database. Reproduces the exact production incident this module
fixes: a table created under the *old* schema (missing `project_id`),
left in place while `metadata` (the *new* schema) gains that column, then
asserts `ensure_schema` reconciles the live table instead of leaving the
column missing.
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from sqlalchemy import MetaData, Table, Column, String, insert, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB

from design_pipeline.db.engine import build_engine
from design_pipeline.db.migrate import ensure_schema
from design_pipeline.db.schema import metadata as db_metadata

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def engine():
    eng = build_engine(TEST_DATABASE_URL)
    db_metadata.drop_all(eng, checkfirst=True)
    yield eng
    db_metadata.drop_all(eng, checkfirst=True)
    eng.dispose()


def _create_old_shape_dependency_graph(engine) -> None:
    """Recreate the exact pre-multi-project `dependency_graph` shape: an
    `id` primary key column that no longer exists in `schema.py` (it was
    replaced by `project_id` itself becoming the primary key), and no
    `project_id` column at all."""
    # `requirements` is JSONB here, matching the live pre-migration table --
    # only the column *set* differed from today's schema (no project_id,
    # a since-removed `id` primary key), never a column's type.
    old_metadata = MetaData()
    Table(
        "dependency_graph", old_metadata,
        Column("id", String, primary_key=True),
        Column("requirements", JSONB, nullable=False),
    )
    old_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO dependency_graph (id, requirements) VALUES ('legacy-row', '{}'::jsonb)"))


def test_ensure_schema_adds_missing_column_to_a_table_that_already_existed(engine):
    # Simulate the exact incident: `artifacts` already exists under an
    # *older* shape (no `project_id`), matching what a table created before
    # multi-project support was added would look like on a live database.
    old_metadata = MetaData()
    Table(
        "artifacts", old_metadata,
        Column("logical_id", String, primary_key=True),
        Column("version", String, primary_key=True),
        Column("type", String, nullable=False),
    )
    old_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO artifacts (logical_id, version, type) VALUES ('demo', '1', 'demo')"))

    inspector = inspect(engine)
    assert "project_id" not in {c["name"] for c in inspector.get_columns("artifacts")}

    db_metadata.create_all(engine, checkfirst=True)  # what every startup already does
    ensure_schema(engine)  # the fix under test

    inspector = inspect(engine)
    live_columns = {c["name"] for c in inspector.get_columns("artifacts")}
    assert "project_id" in live_columns

    # The pre-existing row is backfilled to the single-project default, not
    # dropped or left NULL.
    with engine.connect() as conn:
        row = conn.execute(text("SELECT project_id FROM artifacts WHERE logical_id='demo'")).first()
    assert row.project_id == "default"

    # The primary key widened to match schema.py's declared (project_id,
    # logical_id, version) -- not left as the old two-column key.
    pk_columns = set(inspector.get_pk_constraint("artifacts")["constrained_columns"])
    assert pk_columns == {"project_id", "logical_id", "version"}


def test_ensure_schema_relaxes_a_column_dropped_from_the_declaration(engine):
    # dependency_graph.id existed under the old schema and no longer does --
    # ensure_schema must not error trying to reconcile it, and must leave
    # inserts through the *current* (id-less) table object able to succeed
    # despite id's leftover NOT NULL constraint.
    _create_old_shape_dependency_graph(engine)

    db_metadata.create_all(engine, checkfirst=True)
    ensure_schema(engine)

    inspector = inspect(engine)
    pk_columns = set(inspector.get_pk_constraint("dependency_graph")["constrained_columns"])
    assert pk_columns == {"project_id"}

    from design_pipeline.db.schema import dependency_graph
    # A distinct project_id, not "default" -- the pre-existing legacy row
    # (see _create_old_shape_dependency_graph) was itself backfilled to
    # project_id="default" by ensure_schema, so that value is now taken.
    with engine.begin() as conn:
        conn.execute(insert(dependency_graph).values(project_id="test-project", requirements={}))
        row = conn.execute(select(dependency_graph).where(dependency_graph.c.project_id == "test-project")).mappings().first()
    assert row["requirements"] == {}


def test_ensure_schema_is_a_no_op_on_a_brand_new_database(engine):
    # The common case -- a database create_all just built from scratch --
    # must not be touched or raise.
    db_metadata.create_all(engine, checkfirst=True)
    ensure_schema(engine)
    ensure_schema(engine)  # idempotent: calling twice in a row is also fine

    inspector = inspect(engine)
    assert "project_id" in {c["name"] for c in inspector.get_columns("artifacts")}


def test_runtime_using_postgres_still_works_end_to_end_after_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine):
    # Regression guard for the actual incident: a DesignRuntime pointed at a
    # database whose `artifacts` table predates project_id must be able to
    # initialize, save, and read an artifact -- the exact operation that
    # 500'd in production.
    old_metadata = MetaData()
    Table(
        "artifacts", old_metadata,
        Column("logical_id", String, primary_key=True),
        Column("version", String, primary_key=True),
        Column("type", String, nullable=False),
    )
    old_metadata.create_all(engine, checkfirst=True)

    from design_pipeline.runtime import DesignRuntime

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    runtime = DesignRuntime(tmp_path)
    runtime.initialize("test-project")
    runtime.store.artifacts.save("demo", "demo", {"value": 1}, generated_by={"agent": "stub"})
    assert runtime.store.artifacts.get("demo").content == {"value": 1}
