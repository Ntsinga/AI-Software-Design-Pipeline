"""Regression test for a production incident: `DesignRuntime.restart_generation`
(and anything else gated by `_require_initialized`) crashed with

    [Errno 2] No such file or directory: '.../workflows/design-pipeline.yaml'

on a host with an ephemeral filesystem (Render). In Postgres mode,
`is_initialized()` reflects a durable `project_state` row in the database,
but the agent/workflow YAML config still lives on local disk -- gone after
any redeploy/restart even though Postgres still says the project is
initialized. `_require_initialized` now self-heals any missing default file
before proceeding, the same way `initialize()` writes them the first time.
"""

from pathlib import Path

import pytest

from design_pipeline.runtime import DesignRuntime


def test_a_missing_workflow_file_is_recreated_instead_of_raising(runtime):
    workflow_path = runtime.store.paths.workflows / "design-pipeline.yaml"
    assert workflow_path.exists()
    workflow_path.unlink()  # simulate an ephemeral filesystem wipe after a redeploy

    # Must not raise FileNotFoundError -- this is exactly the call that
    # crashed in production.
    definition = runtime.workflow()
    assert definition.steps
    assert workflow_path.exists()


def test_missing_agent_files_are_recreated_too(runtime):
    for path in runtime.store.paths.agents.glob("*.yaml"):
        path.unlink()
    assert not list(runtime.store.paths.agents.glob("*.yaml"))

    runtime.workflow()  # any call through _require_initialized triggers the self-heal

    assert {p.name for p in runtime.store.paths.agents.glob("*.yaml")} == {"requirements.yaml", "architecture.yaml", "ux.yaml"}


def test_a_customized_agent_file_already_on_disk_is_left_untouched(runtime):
    ux_path = runtime.store.paths.agents / "ux.yaml"
    custom_content = ux_path.read_text(encoding="utf-8") + "\n# a hand-added, project-specific constraint\n"
    ux_path.write_text(custom_content, encoding="utf-8")

    # Wipe only the workflow file -- ux.yaml stays present.
    (runtime.store.paths.workflows / "design-pipeline.yaml").unlink()
    runtime.workflow()

    assert ux_path.read_text(encoding="utf-8") == custom_content


def test_restart_generation_self_heals_before_checking_the_live_provider(tmp_path: Path):
    # restart_generation's own early ValueError ("live generation is not
    # selected") must not mask the self-heal -- it should still recreate
    # the missing file on the way to that check, not crash on it first.
    instance = DesignRuntime(tmp_path)
    instance.initialize("test-project")
    (instance.store.paths.workflows / "design-pipeline.yaml").unlink()

    with pytest.raises(ValueError, match="live generation is not selected"):
        instance.restart_generation()

    assert (instance.store.paths.workflows / "design-pipeline.yaml").exists()
