import pytest

from design_pipeline.models import ArtifactStatus, StepStatus, WorkflowStatus


def test_workflow_pauses_and_resumes_at_approval_gates(runtime):
    first = runtime.run()
    assert first.status == WorkflowStatus.PAUSED
    assert first.pending_approvals == ["system-model"]
    assert runtime.store.artifacts.get("system-model").metadata.status == ArtifactStatus.AWAITING_REVIEW
    assert runtime.store.list_tasks()
    assert runtime.store.list_tasks()[0].handoff.target_agent == "requirements-agent"

    runtime.approve("system-model")
    second = runtime.run()
    assert second.status == WorkflowStatus.PAUSED
    assert second.pending_approvals == ["architecture-model"]

    runtime.approve("architecture-model")
    third = runtime.run()
    assert third.status == WorkflowStatus.COMPLETED
    assert runtime.store.artifacts.get("mockup-spec").metadata.status == ArtifactStatus.GENERATED
    assert runtime.state().step_states["mockups"] == StepStatus.COMPLETED


def test_retry_versions_only_one_artifact(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    original = runtime.store.artifacts.get("architecture-model")
    runtime.add_comment("architecture-model", "Add a boundary note")
    revised = runtime.retry("architecture-model", "Address the review note")
    assert revised.metadata.version == original.metadata.version + 1
    assert revised.metadata.parent_version == original.metadata.version
    assert runtime.store.artifacts.get("brd").metadata.version == 1
    assert runtime.store.artifacts.get("architecture-model").metadata.status == ArtifactStatus.GENERATED
    assert runtime.store.artifacts.get("architecture-model", original.metadata.version).metadata.status == ArtifactStatus.SUPERSEDED
    assert revised.content["feedback_applied"] == ["Add a boundary note"]


def test_request_changes_is_recorded(runtime):
    runtime.run()
    decision = runtime.request_changes("system-model", "Need another actor")
    assert decision.decision == "changes_requested"
    assert runtime.store.artifacts.get("system-model").metadata.status == ArtifactStatus.CHANGES_REQUESTED


def test_individual_ready_step_execution(runtime):
    report = runtime.run_step("inspect-project")
    assert report.completed_steps == ["inspect-project"]
    assert runtime.store.artifacts.get("project-inspection").metadata.version == 1
    with pytest.raises(ValueError, match="dependencies are incomplete"):
        runtime.run_step("requirements-model")


def test_custom_brd_ids_are_traceable(runtime, tmp_path):
    source = tmp_path / "custom.md"
    source.write_text("# BR-017\nDirector approval is required for high-risk reports.", encoding="utf-8")
    runtime.ingest_brd(source)
    report = runtime.run()
    assert report.pending_approvals == ["system-model"]
    assert runtime.store.artifacts.get("brd").metadata.requirements == ["BR-017"]
    assert "system-model" in runtime.dependencies("BR-017")


def test_repeated_workflow_run_does_not_create_new_artifact_versions(runtime):
    runtime.run()
    first_versions = {item.logical_id: item.version for item in runtime.store.artifacts.list_latest()}
    runtime.run()
    second_versions = {item.logical_id: item.version for item in runtime.store.artifacts.list_latest()}
    assert first_versions == second_versions


def test_failed_stage_can_be_retried_without_restarting_completed_steps(runtime):
    runtime.run()
    runtime.approve("system-model")
    agent_path = runtime.store.paths.agents / "architecture.yaml"
    original = agent_path.read_text(encoding="utf-8")
    agent_path.write_text(original.replace("id: architecture-agent", "id: unknown-agent"), encoding="utf-8")
    try:
        failed = runtime.run()
        assert failed.failed_step == "architecture"
        assert runtime.state().step_states["requirements"] == StepStatus.COMPLETED
        assert any(event.event_type == "STEP_FAILED" and event.step_id == "architecture" for event in runtime.store.read_events())
        agent_path.write_text(original, encoding="utf-8")
        retried = runtime.run()
        assert retried.status == WorkflowStatus.PAUSED
        assert retried.pending_approvals == ["architecture-model"]
    finally:
        agent_path.write_text(original, encoding="utf-8")
