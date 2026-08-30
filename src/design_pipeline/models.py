"""Validated domain contracts for the Design Pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactStatus(str, Enum):
    DRAFT = "draft"
    GENERATING = "generating"
    GENERATED = "generated"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class WorkflowStatus(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    AWAITING_REVIEW = "awaiting_review"
    FAILED = "failed"


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^BR-\d{3,}$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    status: Literal["proposed", "approved", "changed", "retired"] = "proposed"


class BusinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actors: list[str] = Field(default_factory=list)
    stakeholders: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    processes: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    responsibilities: dict[str, list[str]] = Field(default_factory=dict)
    external_organizations: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)


class SolutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilities: list[str] = Field(default_factory=list)
    application_boundaries: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    data_ownership: dict[str, str] = Field(default_factory=dict)
    user_capabilities: list[str] = Field(default_factory=list)
    external_dependencies: list[str] = Field(default_factory=list)
    security_concepts: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class SystemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[str] = Field(default_factory=list)
    business_capabilities: list[str] = Field(default_factory=list)
    business_workflows: list[str] = Field(default_factory=list)
    system_capabilities: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    screens: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    permissions: dict[str, list[str]] = Field(default_factory=dict)
    traceability: dict[str, list[str]] = Field(default_factory=dict)


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_id: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=1)

    @property
    def uri(self) -> str:
        return f"artifact://{self.logical_id}/v{self.version}" if self.version else f"artifact://{self.logical_id}"


class GeneratorInfo(BaseModel):
    agent: str
    provider: str = "stub"
    model: str = "deterministic-fixture"
    generated_at: datetime = Field(default_factory=utc_now)


class ArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: ArtifactStatus = ArtifactStatus.DRAFT
    parent_version: int | None = Field(default=None, ge=1)
    inputs: list[ArtifactReference] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    generated_by: GeneratorInfo
    content_file: str
    comments: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    error: str | None = None


class StoredArtifact(BaseModel):
    metadata: ArtifactMetadata
    content: Any


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: Literal["deterministic", "agent", "human-approval"]
    agent: str | None = None
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    retry_limit: int = Field(default=1, ge=0)

    @field_validator("agent")
    @classmethod
    def agent_required_for_agent_step(cls, value: str | None, info: Any) -> str | None:
        if info.data.get("type") == "agent" and not value:
            raise ValueError("agent is required for agent workflow steps")
        return value


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    steps: list[WorkflowStep] = Field(min_length=1)

    @field_validator("steps")
    @classmethod
    def unique_step_ids_and_dependencies(cls, steps: list[WorkflowStep]) -> list[WorkflowStep]:
        ids = [step.id for step in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow step IDs must be unique")
        known = set(ids)
        for step in steps:
            missing = set(step.depends_on) - known
            if missing:
                raise ValueError(f"step {step.id} depends on unknown steps: {sorted(missing)}")
        return steps


class Handoff(BaseModel):
    task_id: str
    source_agent: str
    target_agent: str
    objective: str
    inputs: dict[str, str] = Field(default_factory=dict)
    changes: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)


class Task(BaseModel):
    id: str
    objective: str
    step_id: str
    handoff: Handoff | None = None
    status: StepStatus = StepStatus.PENDING
    attempts: int = Field(default=0, ge=0)


class Comment(BaseModel):
    id: str
    artifact_id: str
    text: str = Field(min_length=1)
    author: str = "user"
    location: dict[str, Any] | None = None
    status: Literal["open", "resolved"] = "open"
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class Approval(BaseModel):
    id: str
    artifact_id: str
    version: int = Field(ge=1)
    decision: Literal["approved", "changes_requested"]
    reviewer: str = "user"
    note: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class StepResult(BaseModel):
    step_id: str
    status: StepStatus
    artifact_ids: list[str] = Field(default_factory=list)
    message: str = ""
    error: str | None = None


class ExecutionEvent(BaseModel):
    event_id: str
    event_type: str
    timestamp: datetime = Field(default_factory=utc_now)
    step_id: str | None = None
    artifact_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProjectState(BaseModel):
    project_id: str
    workflow_id: str = "initial-design"
    workflow_status: WorkflowStatus = WorkflowStatus.NOT_STARTED
    step_states: dict[str, StepStatus] = Field(default_factory=dict)
    pending_approvals: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class DependencyGraph(BaseModel):
    requirements: dict[str, list[str]] = Field(default_factory=dict)


class RunReport(BaseModel):
    status: WorkflowStatus
    completed_steps: list[str] = Field(default_factory=list)
    pending_approvals: list[str] = Field(default_factory=list)
    failed_step: str | None = None
    message: str = ""

