from design_pipeline.models import ArtifactReference, ArtifactStatus
import pytest


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
