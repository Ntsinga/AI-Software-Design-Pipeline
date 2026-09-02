"""Regression test for a production incident: `DesignRuntime.restart_generation`
(and anything else gated by `_require_initialized`) crashed with

    [Errno 2] No such file or directory: '.../workflows/design-pipeline.yaml'

on a host with an ephemeral filesystem (Render). In Postgres mode,
`is_initialized()` reflects a durable `project_state` row in the database,
but the agent/workflow YAML config still lives on local disk -- gone after
any redeploy/restart even though Postgres still says the project is
initialized. `_require_initialized` now self-heals any missing default file
before proceeding, the same way `initialize()` writes them the first time.

The self-heal itself only actually runs once per `DesignRuntime` instance
(a real ephemeral-disk wipe only ever happens between process restarts --
nothing wipes a running process's own disk out from under it -- and
re-running it on every single request through this method, which sits
under ~20 others, was a real production performance bug: one page load's
worth of API calls paid its Postgres-round-trip-plus-file-reads cost that
many times over). So these tests simulate the wipe the way it actually
happens -- delete the files, then construct a FRESH `DesignRuntime` against
the same root/project, exactly as a redeploy restarts the process -- rather
than deleting files and calling a method on the same already-running
instance, which no longer models anything that happens in production.
"""

from pathlib import Path

import pytest

from design_pipeline.runtime import DesignRuntime


def test_a_missing_workflow_file_is_recreated_instead_of_raising(tmp_path: Path):
    first = DesignRuntime(tmp_path)
    first.initialize("test-project")
    workflow_path = first.store.paths.workflows / "design-pipeline.yaml"
    assert workflow_path.exists()
    workflow_path.unlink()  # simulate an ephemeral filesystem wipe after a redeploy

    # A fresh instance against the same root, exactly as a restarted process
    # would construct one -- must not raise FileNotFoundError, which is
    # exactly the call that crashed in production.
    second = DesignRuntime(tmp_path)
    definition = second.workflow()
    assert definition.steps
    assert workflow_path.exists()


def test_missing_agent_files_are_recreated_too(tmp_path: Path):
    first = DesignRuntime(tmp_path)
    first.initialize("test-project")
    for path in first.store.paths.agents.glob("*.yaml"):
        path.unlink()
    assert not list(first.store.paths.agents.glob("*.yaml"))

    second = DesignRuntime(tmp_path)
    second.workflow()  # any call through _require_initialized triggers the self-heal

    assert {p.name for p in second.store.paths.agents.glob("*.yaml")} == {"requirements.yaml", "architecture.yaml", "ux.yaml"}


def test_a_customized_agent_file_already_on_disk_is_left_untouched(tmp_path: Path):
    first = DesignRuntime(tmp_path)
    first.initialize("test-project")
    ux_path = first.store.paths.agents / "ux.yaml"
    custom_content = ux_path.read_text(encoding="utf-8") + "\n# a hand-added, project-specific constraint\n"
    ux_path.write_text(custom_content, encoding="utf-8")

    # Wipe only the workflow file -- ux.yaml stays present.
    (first.store.paths.workflows / "design-pipeline.yaml").unlink()

    second = DesignRuntime(tmp_path)
    second.workflow()

    assert ux_path.read_text(encoding="utf-8") == custom_content


def test_restart_generation_self_heals_before_checking_the_live_provider(tmp_path: Path):
    # restart_generation's own early ValueError ("live generation is not
    # selected") must not mask the self-heal -- it should still recreate
    # the missing file on the way to that check, not crash on it first.
    first = DesignRuntime(tmp_path)
    first.initialize("test-project")
    (first.store.paths.workflows / "design-pipeline.yaml").unlink()

    second = DesignRuntime(tmp_path)
    with pytest.raises(ValueError, match="live generation is not selected"):
        second.restart_generation()

    assert (second.store.paths.workflows / "design-pipeline.yaml").exists()


def test_the_self_heal_does_not_repeat_within_the_same_process(tmp_path: Path):
    """The actual performance fix: once one DesignRuntime instance has
    self-healed, deleting a file again mid-process must NOT trigger another
    reconciliation pass -- nothing else touches this process's own disk
    between requests, so there's nothing to re-check. (This is a filesystem-
    mode test since the expensive half of _sync_config only exists in
    Postgres mode, but the once-per-instance guard applies unconditionally.)
    """
    instance = DesignRuntime(tmp_path)
    instance.initialize("test-project")
    assert instance._config_synced is True

    workflow_path = instance.store.paths.workflows / "design-pipeline.yaml"
    workflow_path.unlink()
    # Already synced once -- _require_initialized skips reconciliation this
    # time, so this now raises exactly like it did before self-heal existed,
    # rather than silently re-fixing something nothing in this process could
    # actually have broken.
    with pytest.raises(FileNotFoundError):
        instance.workflow()
    assert not workflow_path.exists()
