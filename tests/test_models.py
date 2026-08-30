import pytest
from pydantic import ValidationError

from design_pipeline.models import ArtifactMetadata, ArtifactStatus, Requirement, WorkflowDefinition


def test_requirement_id_is_validated():
    assert Requirement(id="BR-001", title="A", description="B").id == "BR-001"
    with pytest.raises(ValidationError):
        Requirement(id="REQ-1", title="A", description="B")


def test_workflow_rejects_unknown_dependency():
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate({"id": "x", "name": "X", "steps": [{"id": "a", "name": "A", "type": "deterministic", "depends_on": ["missing"]}]})


def test_artifact_metadata_has_lifecycle_state():
    metadata = ArtifactMetadata(logical_id="x", type="x", version=1, generated_by={"agent": "stub"}, content_file="v1.json")
    assert metadata.status == ArtifactStatus.DRAFT

