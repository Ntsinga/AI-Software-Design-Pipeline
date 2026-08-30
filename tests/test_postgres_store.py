"""Exercises the Postgres-backed store against a real database.

Skipped by default: requires both the optional `postgres` extra
(`pip install ".[postgres]"`) and a `TEST_DATABASE_URL` pointing at a
throwaway Postgres database (a Neon test branch works well -- nothing here
is destructive to anything but its own tables, but don't point it at data
you care about; the fixture drops and recreates every table in this schema
before and after each test).
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from design_pipeline.db.engine import build_engine
from design_pipeline.db.schema import metadata as db_metadata
from design_pipeline.models import ArtifactReference, ArtifactStatus
from design_pipeline.runtime import DesignRuntime

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DesignRuntime:
    engine = build_engine(TEST_DATABASE_URL)
    db_metadata.drop_all(engine, checkfirst=True)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    instance = DesignRuntime(tmp_path)
    instance.initialize("test-project")
    yield instance
    db_metadata.drop_all(engine, checkfirst=True)
    engine.dispose()


def test_uses_postgres_backend(runtime):
    from design_pipeline.db.store import PostgresProjectStore

    assert isinstance(runtime.store, PostgresProjectStore)


def test_registry_versions_and_parent_linkage(runtime):
    first = runtime.store.artifacts.save("demo", "demo", {"value": 1}, generated_by={"agent": "stub"})
    second = runtime.store.artifacts.save("demo", "demo", {"value": 2}, generated_by={"agent": "stub"}, inputs=[ArtifactReference(logical_id="demo", version=1)])
    assert first.metadata.version == 1
    assert second.metadata.version == 2
    assert second.metadata.parent_version == 1
    assert runtime.store.artifacts.get("demo").content == {"value": 2}
    assert runtime.store.artifacts.get("demo", 1).metadata.status == ArtifactStatus.GENERATED


def test_comments_and_events_are_durable(runtime):
    runtime.store.artifacts.save("demo", "demo", "content", generated_by={"agent": "stub"})
    comment = runtime.add_comment("demo", "Please revise this")
    assert runtime.store.list_comments("demo")[0].id == comment.id
    assert any(event.event_type == "COMMENT_ADDED" for event in runtime.store.read_events())


def test_approval_is_linked_to_artifact_metadata(runtime):
    runtime.store.artifacts.save("demo", "demo", "content", generated_by={"agent": "stub"})
    approval = runtime.approve("demo")
    artifact = runtime.store.artifacts.get("demo")
    assert approval.id in artifact.metadata.approvals


def test_invalid_artifact_transition_is_rejected(runtime):
    runtime.store.artifacts.save("demo", "demo", "content", generated_by={"agent": "stub"}, status=ArtifactStatus.APPROVED)
    with pytest.raises(ValueError, match="invalid artifact status transition"):
        runtime.store.artifacts.update_status("demo", ArtifactStatus.GENERATING)


def test_state_survives_a_fresh_store_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    first = DesignRuntime(tmp_path)
    first.initialize("test-project")
    first.store.artifacts.save("demo", "demo", {"value": 1}, generated_by={"agent": "stub"})

    # A second, independent runtime pointed at the same DATABASE_URL --
    # simulating a redeploy/restart -- must see the same data.
    second = DesignRuntime(tmp_path)
    assert second.store.is_initialized()
    assert second.store.artifacts.get("demo").content == {"value": 1}
