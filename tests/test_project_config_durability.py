"""Exercises the Postgres-backed durability of agent/workflow config, the
staged BRD, and the deployment-wide provider setting -- the follow-up fix to
`_require_initialized`'s file-level self-heal (test_runtime_self_heal.py):
that self-heal only regenerates *default* content on an ephemeral
filesystem, silently discarding any real customization. These tests confirm
Postgres is now the actual source of truth for that content, not just local
disk.

Skipped by default -- same requirements as the other Postgres-backed tests:
the `postgres` extra installed and `TEST_DATABASE_URL` set to a throwaway
database.
"""

import os
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from design_pipeline.db.engine import build_engine
from design_pipeline.db.schema import metadata as db_metadata
from design_pipeline.provider_config import load_provider_settings, update_provider
from design_pipeline.runtime import DesignRuntime

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def engine():
    eng = build_engine(TEST_DATABASE_URL)
    db_metadata.drop_all(eng, checkfirst=True)
    yield eng
    db_metadata.drop_all(eng, checkfirst=True)
    eng.dispose()


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine) -> DesignRuntime:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    instance = DesignRuntime(tmp_path)
    instance.initialize("test-project")
    return instance


def test_initializing_seeds_the_db_with_the_default_config(runtime):
    config = runtime.store.load_config()
    assert config is not None
    assert set(config["agent_files"]) == {"requirements.yaml", "architecture.yaml", "ux.yaml"}
    assert "id: ux-agent" in config["agent_files"]["ux.yaml"]
    assert "id: initial-design" in config["workflow_file"]


def test_a_real_customization_survives_disk_being_wiped(runtime, tmp_path):
    # Simulate hand-editing an agent's prompt (exactly what happened this
    # session, editing ux.yaml directly) -- must persist through the
    # project_config sync path, not just re-seed the stock default.
    ux_path = runtime.store.paths.agents / "ux.yaml"
    customized = ux_path.read_text(encoding="utf-8") + "\n  - a hand-added, project-specific constraint\n"
    ux_path.write_text(customized, encoding="utf-8")
    runtime._sync_config()  # the write path: pushes the customization into Postgres

    # Simulate the ephemeral-disk wipe: every local file gone. This only
    # ever actually happens between process restarts, not mid-process (the
    # self-heal in _require_initialized runs once per DesignRuntime
    # instance, not once per request -- see its own comment) -- so restore
    # is verified against a fresh instance, exactly as a redeploy would
    # construct one, not by reusing the already-synced `runtime` fixture.
    for path in runtime.store.paths.agents.glob("*.yaml"):
        path.unlink()
    (runtime.store.paths.workflows / "design-pipeline.yaml").unlink()

    restarted = DesignRuntime(tmp_path)
    restarted.workflow()  # triggers _require_initialized -> _sync_config's restore path

    assert (restarted.store.paths.agents / "ux.yaml").read_text(encoding="utf-8") == customized


def test_a_staged_brd_survives_disk_being_wiped(runtime, tmp_path):
    runtime.ingest_brd_text("# Business Requirements\n\nSome staged content.", "BRD.md")
    brd_path = runtime.store.paths.input / "BRD.md"
    assert brd_path.exists()

    brd_path.unlink()  # simulate the wipe (see the comment above)
    restarted = DesignRuntime(tmp_path)
    restarted.workflow()  # any _require_initialized call triggers the restore

    assert brd_path.exists()
    assert "Some staged content" in brd_path.read_text(encoding="utf-8")


def test_a_staged_brd_survives_the_wipe_regardless_of_its_original_filename(runtime, tmp_path):
    """The test above happens to upload as "BRD.md" -- the exact filename
    DocumentReader.read_brd() always reads back, on every backend. Every
    real upload instead keeps its own original name (AuditModule.docx,
    say) purely for display (staged_brd_filename, used by the UI's source
    banner and History) -- documents.py's ingest_*() always writes the
    extracted text to the SAME fixed path, input/BRD.md, regardless.
    Restoring to a path built from the original filename instead of that
    fixed name wrote a file read_brd() would never find: self-heal
    "succeeded" (no exception, brd_path.exists() was even True afterward)
    while leaving the real document permanently invisible to every live
    provider call downstream. Root-caused a real production incident this
    way -- brd kept regenerating a generic "no document uploaded" fallback
    on every retry, on every fresh deploy, forever, because self-heal
    never restored where read_brd() would actually look."""
    runtime.ingest_brd_text("# Business Requirements\n\nAudit plan details.", "AuditModule.md")
    brd_path = runtime.store.paths.input / "BRD.md"
    assert brd_path.exists()

    brd_path.unlink()  # simulate the wipe (see the comment above)
    restarted = DesignRuntime(tmp_path)
    restarted.workflow()  # any _require_initialized call triggers the restore

    assert brd_path.exists()
    assert "Audit plan details" in brd_path.read_text(encoding="utf-8")
    # And the one thing that actually reads it back must find it too.
    document = restarted._project_inspection_content("test-project").get("staged_document")
    assert document is not None
    assert "Audit plan details" in document["text"]


def test_provider_selection_survives_local_env_being_wiped(runtime):
    update_provider(runtime.root, "gemini", database_url=runtime._database_url)
    env_path = runtime.root / ".env"
    assert "DESIGN_PIPELINE_PROVIDER=gemini" in env_path.read_text(encoding="utf-8")

    env_path.unlink()  # simulate the wipe -- no real env var set either
    settings = load_provider_settings(runtime.root, environ={}, database_url=runtime._database_url)

    assert settings.provider == "gemini"


def test_a_real_env_var_still_wins_over_the_db_value(runtime):
    update_provider(runtime.root, "gemini", database_url=runtime._database_url)
    settings = load_provider_settings(runtime.root, environ={"DESIGN_PIPELINE_PROVIDER": "openai", "OPENAI_API_KEY": "x", "OPENAI_MODEL": "gpt-4o"}, database_url=runtime._database_url)
    assert settings.provider == "openai"
