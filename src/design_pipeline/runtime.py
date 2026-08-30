"""Shared runtime used by the CLI and HTTP API."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .agents import AgentLoader, DeterministicAgent, ProviderBackedAgent, create_handoff
from .documents import DocumentReader
from .models import (
    Approval,
    ArtifactReference,
    ArtifactStatus,
    Comment,
    ProjectState,
    RunReport,
    StepStatus,
    Task,
    WorkflowStatus,
    utc_now,
)
from .storage import atomic_write, build_project_store
from .provider_config import load_provider_settings
from .providers import create_model_provider


DEFAULT_AGENT_FILES = {
    "requirements.yaml": "id: requirements-agent\ndescription: Build the BRD and progressively richer business, solution, and system models.\ninputs: [project-inspection, brd]\noutputs: [brd, business-model, solution-model, system-model]\ntools: [project.read, artifact.read, artifact.write]\nconstraints:\n  - Preserve requirement intent.\n  - Produce traceable structured models.\n",
    "architecture.yaml": "id: architecture-agent\ndescription: Analyze an approved system model and recommend the smallest useful design set.\ninputs: [brd, business-model, solution-model, system-model]\noutputs: [architecture-model, diagram-recommendations]\ntools: [artifact.read, artifact.write, mermaid.render]\nconstraints:\n  - Do not modify approved business requirements.\n  - Prefer the smallest useful set of diagrams.\n",
    "ux.yaml": "id: ux-agent\ndescription: Produce a lightweight interactive mockup specification from approved design artifacts.\ninputs: [brd, system-model, architecture-model]\noutputs: [mockup-spec]\ntools: [artifact.read, artifact.write]\nconstraints:\n  - Use synthetic data.\n  - Optimize for workflow validation rather than production UI quality.\n",
}

DEFAULT_WORKFLOW = """id: initial-design
name: Initial Design
steps:
  - id: inspect-project
    name: Inspect project
    type: deterministic
    outputs: [project-inspection]
  - id: requirements
    name: Generate requirements baseline
    type: agent
    agent: requirements-agent
    inputs: [project-inspection]
    outputs: [brd]
    depends_on: [inspect-project]
  - id: requirements-model
    name: Build business, solution, and system models
    type: agent
    agent: requirements-agent
    inputs: [brd]
    outputs: [business-model, solution-model, system-model]
    depends_on: [requirements]
  - id: requirements-approval
    name: Approve requirements model
    type: human-approval
    inputs: [system-model]
    depends_on: [requirements-model]
  - id: architecture
    name: Generate architecture recommendations
    type: agent
    agent: architecture-agent
    inputs: [brd, business-model, solution-model, system-model]
    outputs: [architecture-model, diagram-recommendations]
    depends_on: [requirements-approval]
  - id: architecture-approval
    name: Approve architecture
    type: human-approval
    inputs: [architecture-model]
    depends_on: [architecture]
  - id: mockups
    name: Generate mockup specification
    type: agent
    agent: ux-agent
    inputs: [brd, system-model, architecture-model]
    outputs: [mockup-spec]
    depends_on: [architecture-approval]
"""


class DesignRuntime:
    """Orchestrate workflows while keeping persistence in :class:`ProjectStore`."""

    def __init__(self, root: Path | str):
        self.store = build_project_store(root)

    @property
    def root(self) -> Path:
        return self.store.paths.root

    def initialize(self, project_id: str | None = None) -> ProjectState:
        state = self.store.initialize(project_id)
        self._write_defaults()
        if not self.store.read_events():
            self.store.append_event("PROJECT_INITIALIZED", details={"project_id": state.project_id})
        return state

    def _write_defaults(self) -> None:
        for name, content in DEFAULT_AGENT_FILES.items():
            path = self.store.paths.agents / name
            if not path.exists():
                atomic_write(path, content)
        workflow = self.store.paths.workflows / "design-pipeline.yaml"
        if not workflow.exists():
            atomic_write(workflow, DEFAULT_WORKFLOW)

    def _require_initialized(self) -> None:
        if not self.store.is_initialized():
            raise FileNotFoundError("project is not initialized; run `design init`")

    def workflow(self):
        self._require_initialized()
        path = self.store.paths.workflows / "design-pipeline.yaml"
        from .models import WorkflowDefinition
        return WorkflowDefinition.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def state(self) -> ProjectState:
        self._require_initialized()
        return self.store.load_state()

    def status(self) -> dict[str, Any]:
        state = self.state()
        return {
            "project_id": state.project_id,
            "workflow_id": state.workflow_id,
            "workflow_status": state.workflow_status.value,
            "steps": {key: value.value for key, value in state.step_states.items()},
            "pending_approvals": state.pending_approvals,
            "artifacts": [item.model_dump(mode="json") for item in self.store.artifacts.list_latest()],
            "tasks": [item.model_dump(mode="json") for item in self.store.list_tasks()],
            "provider": load_provider_settings(self.root).public_status(),
        }

    def ingest_brd(self, source: Path | str):
        self._require_initialized()
        document = DocumentReader(self.root).ingest_brd(Path(source))
        self.store.append_event("BRD_INGESTED", details={"filename": document.filename, "path": document.path})
        return document

    def ingest_brd_text(self, content: str, filename: str = "BRD.md"):
        self._require_initialized()
        document = DocumentReader(self.root).ingest_text(content, filename)
        self.store.append_event("BRD_INGESTED", details={"filename": document.filename, "path": document.path})
        return document

    def ingest_brd_bytes(self, content: bytes, filename: str):
        self._require_initialized()
        document = DocumentReader(self.root).ingest_bytes(content, filename)
        self.store.append_event("BRD_INGESTED", details={"filename": document.filename, "path": document.path})
        return document

    def _ordered_steps(self):
        steps = {step.id: step for step in self.workflow().steps}
        ordered = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            if step_id in visiting:
                raise ValueError("workflow contains a dependency cycle")
            visiting.add(step_id)
            for dependency in steps[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)
            ordered.append(steps[step_id])

        for step_id in steps:
            visit(step_id)
        return ordered

    def _load_inputs(self, names: list[str]) -> tuple[dict[str, Any], list[ArtifactReference], list[str]]:
        values: dict[str, Any] = {}
        references: list[ArtifactReference] = []
        requirements: set[str] = set()
        for name in names:
            try:
                artifact = self.store.artifacts.get(name)
            except FileNotFoundError:
                continue
            values[name] = artifact.content
            references.append(ArtifactReference(logical_id=name, version=artifact.metadata.version))
            requirements.update(artifact.metadata.requirements)
        return values, references, sorted(requirements)

    @staticmethod
    def _requirements_from_content(content: Any) -> list[str]:
        return sorted(set(re.findall(r"BR-\d{3,}", str(content))))

    def _save_step_outputs(self, step, values: dict[str, Any], references: list[ArtifactReference], requirements: list[str], generated_by: dict[str, str]) -> list[str]:
        artifact_ids: list[str] = []
        agent_id = generated_by["agent"]
        for output in step.outputs:
            content = values.get(output)
            if content is None:
                raise ValueError(f"agent {agent_id} did not produce declared output {output}")
            output_requirements = requirements or self._requirements_from_content(content)
            try:
                parent_version = self.store.artifacts.get(output).metadata.version
            except FileNotFoundError:
                parent_version = None
            artifact = self.store.artifacts.save(output, output, content, generated_by=generated_by, inputs=references, requirements=output_requirements, parent_version=parent_version)
            artifact_ids.append(artifact.metadata.logical_id)
            graph = self.store.load_dependency_graph()
            for requirement in output_requirements:
                graph.requirements.setdefault(requirement, [])
                if output not in graph.requirements[requirement]:
                    graph.requirements[requirement].append(output)
            self.store.save_dependency_graph(graph)
            self.store.append_event("ARTIFACT_GENERATED", step_id=step.id, artifact_id=output, details={"version": artifact.metadata.version, **generated_by})
        return artifact_ids

    def _execute_agent(self, agent_id: str, outputs: list[str], inputs: dict[str, Any], comments: list[Comment] | None = None, instruction: str | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        definition = AgentLoader(self.store.paths.agents).load(agent_id)
        settings = load_provider_settings(self.root)
        if settings.provider == "stub":
            values = DeterministicAgent(definition, self.root).run(outputs, inputs, comments, instruction)
            return values, {"agent": agent_id, "provider": "stub", "model": "deterministic-fixture"}
        provider = create_model_provider(settings)
        values = ProviderBackedAgent(definition, provider).run(outputs, inputs, comments, instruction)
        return values, {"agent": agent_id, "provider": provider.name, "model": provider.model}

    def _start_task(self, step, references: list[ArtifactReference]) -> Task | None:
        if step.type != "agent" or not step.agent:
            return None
        task = Task(
            id=f"task-{step.id}-{uuid4().hex[:8]}",
            objective=step.name,
            step_id=step.id,
            handoff=create_handoff(
                "workflow-engine",
                step.agent,
                step.name,
                {reference.logical_id: reference.uri for reference in references},
                step.outputs,
                constraints=["Use only the referenced project artifacts and declared capabilities."],
                task_id=f"task-{step.id}",
            ),
            status=StepStatus.RUNNING,
            attempts=1,
        )
        self.store.save_task(task)
        self.store.append_event("TASK_CREATED", step_id=step.id, details={"task_id": task.id, "target_agent": step.agent})
        return task

    def run(self, step_id: str | None = None) -> RunReport:
        self._require_initialized()
        if step_id is not None:
            return self.run_step(step_id)
        state = self.store.load_state()
        state.workflow_status = WorkflowStatus.RUNNING
        self.store.save_state(state)
        completed: list[str] = []
        for step in self._ordered_steps():
            current_status = state.step_states.get(step.id, StepStatus.PENDING)
            if current_status == StepStatus.COMPLETED:
                continue
            if any(state.step_states.get(dependency) != StepStatus.COMPLETED for dependency in step.depends_on):
                continue
            if step.type == "human-approval":
                pending: list[str] = []
                for artifact_id in step.inputs:
                    artifact = self.store.artifacts.get(artifact_id)
                    if artifact.metadata.status != ArtifactStatus.APPROVED:
                        self.store.artifacts.update_status(artifact_id, ArtifactStatus.AWAITING_REVIEW, artifact.metadata.version)
                        pending.append(artifact_id)
                if pending:
                    state.step_states[step.id] = StepStatus.AWAITING_REVIEW
                    state.pending_approvals = pending
                    state.workflow_status = WorkflowStatus.PAUSED
                    self.store.save_state(state)
                    self.store.append_event("APPROVAL_REQUESTED", step_id=step.id, details={"artifacts": pending})
                    return RunReport(status=state.workflow_status, completed_steps=completed, pending_approvals=pending, message=f"Workflow paused for approval: {', '.join(pending)}")
                state.step_states[step.id] = StepStatus.COMPLETED
                state.pending_approvals = []
                completed.append(step.id)
                self.store.append_event("APPROVAL_COMPLETED", step_id=step.id)
                continue
            state.step_states[step.id] = StepStatus.RUNNING
            self.store.save_state(state)
            task = None
            try:
                inputs, references, requirements = self._load_inputs(step.inputs)
                task = self._start_task(step, references)
                if step.type == "deterministic":
                    values = {"project-inspection": {"project_id": state.project_id, "root": str(self.root), "design_directory": str(self.store.paths.design)}}
                    generated_by = {"agent": "runtime", "provider": "runtime", "model": "deterministic"}
                else:
                    if not step.agent:
                        raise ValueError(f"agent step {step.id} has no agent")
                    values, generated_by = self._execute_agent(step.agent, step.outputs, inputs)
                artifact_ids = self._save_step_outputs(step, values, references, requirements, generated_by)
                if task:
                    task.status = StepStatus.COMPLETED
                    self.store.save_task(task)
                    self.store.append_event("TASK_COMPLETED", step_id=step.id, details={"task_id": task.id})
                state.step_states[step.id] = StepStatus.COMPLETED
                self.store.save_state(state)
                self.store.append_event("STEP_COMPLETED", step_id=step.id, details={"artifacts": artifact_ids})
                completed.append(step.id)
            except Exception as exc:
                if "task" in locals() and task:
                    task.status = StepStatus.FAILED
                    self.store.save_task(task)
                state.step_states[step.id] = StepStatus.FAILED
                state.workflow_status = WorkflowStatus.FAILED
                self.store.save_state(state)
                self.store.append_event("STEP_FAILED", step_id=step.id, details={"error": str(exc)})
                return RunReport(status=state.workflow_status, completed_steps=completed, failed_step=step.id, message=str(exc))
        state.workflow_status = WorkflowStatus.COMPLETED
        state.pending_approvals = []
        self.store.save_state(state)
        self.store.append_event("WORKFLOW_COMPLETED", details={"workflow_id": state.workflow_id})
        return RunReport(status=state.workflow_status, completed_steps=completed, message="Workflow completed")

    def restart_generation(self) -> RunReport:
        """Restart downstream design generation with the configured live provider.

        Existing artifacts remain intact. Each regenerated artifact is written as
        a new version linked to the version it replaced.
        """
        self._require_initialized()
        settings = load_provider_settings(self.root)
        if settings.provider == "stub":
            raise ValueError("live generation is not selected; set DESIGN_PIPELINE_PROVIDER to openai or anthropic in .env, then restart the server")
        state = self.store.load_state()
        reset_started = False
        reset_steps: list[str] = []
        for step in self._ordered_steps():
            if step.id == "requirements-model":
                reset_started = True
            if reset_started and step.type in {"agent", "human-approval"}:
                state.step_states[step.id] = StepStatus.PENDING
                reset_steps.append(step.id)
        state.pending_approvals = []
        state.workflow_status = WorkflowStatus.RUNNING
        self.store.save_state(state)
        self.store.append_event("GENERATION_RESTARTED", details={"provider": settings.provider, "model": settings.model, "steps": reset_steps})
        return self.run()

    def run_step(self, step_id: str) -> RunReport:
        """Execute exactly one ready step, preserving the surrounding workflow state."""
        self._require_initialized()
        state = self.store.load_state()
        step = next((candidate for candidate in self.workflow().steps if candidate.id == step_id), None)
        if step is None:
            raise ValueError(f"unknown workflow step: {step_id}")
        if state.step_states.get(step.id) == StepStatus.COMPLETED:
            return RunReport(status=state.workflow_status, completed_steps=[step.id], message="Step already completed")
        if any(state.step_states.get(dependency) != StepStatus.COMPLETED for dependency in step.depends_on):
            raise ValueError(f"step {step.id} is not ready; dependencies are incomplete")
        if step.type == "human-approval":
            pending: list[str] = []
            for artifact_id in step.inputs:
                artifact = self.store.artifacts.get(artifact_id)
                if artifact.metadata.status != ArtifactStatus.APPROVED:
                    self.store.artifacts.update_status(artifact_id, ArtifactStatus.AWAITING_REVIEW, artifact.metadata.version)
                    pending.append(artifact_id)
            if pending:
                state.step_states[step.id] = StepStatus.AWAITING_REVIEW
                state.pending_approvals = pending
                state.workflow_status = WorkflowStatus.PAUSED
                self.store.save_state(state)
                self.store.append_event("APPROVAL_REQUESTED", step_id=step.id, details={"artifacts": pending})
                return RunReport(status=state.workflow_status, pending_approvals=pending, message=f"Workflow paused for approval: {', '.join(pending)}")
            state.step_states[step.id] = StepStatus.COMPLETED
            state.pending_approvals = []
            self.store.save_state(state)
            self.store.append_event("APPROVAL_COMPLETED", step_id=step.id)
            return RunReport(status=state.workflow_status, completed_steps=[step.id], message="Approval gate completed")
        state.step_states[step.id] = StepStatus.RUNNING
        self.store.save_state(state)
        task = None
        try:
            inputs, references, requirements = self._load_inputs(step.inputs)
            task = self._start_task(step, references)
            if step.type == "deterministic":
                values = {"project-inspection": {"project_id": state.project_id, "root": str(self.root), "design_directory": str(self.store.paths.design)}}
                generated_by = {"agent": "runtime", "provider": "runtime", "model": "deterministic"}
            else:
                if not step.agent:
                    raise ValueError(f"agent step {step.id} has no agent")
                values, generated_by = self._execute_agent(step.agent, step.outputs, inputs)
            artifact_ids = self._save_step_outputs(step, values, references, requirements, generated_by)
            if task:
                task.status = StepStatus.COMPLETED
                self.store.save_task(task)
                self.store.append_event("TASK_COMPLETED", step_id=step.id, details={"task_id": task.id})
        except Exception as exc:
            if "task" in locals() and task:
                task.status = StepStatus.FAILED
                self.store.save_task(task)
            state.step_states[step.id] = StepStatus.FAILED
            state.workflow_status = WorkflowStatus.FAILED
            self.store.save_state(state)
            self.store.append_event("STEP_FAILED", step_id=step.id, details={"error": str(exc)})
            return RunReport(status=state.workflow_status, failed_step=step.id, message=str(exc))
        state.step_states[step.id] = StepStatus.COMPLETED
        if state.workflow_status == WorkflowStatus.NOT_STARTED:
            state.workflow_status = WorkflowStatus.RUNNING
        self.store.save_state(state)
        self.store.append_event("STEP_COMPLETED", step_id=step.id, details={"artifacts": artifact_ids})
        return RunReport(status=state.workflow_status, completed_steps=[step.id], message=f"Step completed: {step.id}")

    def approve(self, artifact_id: str, version: int | None = None, reviewer: str = "user", note: str | None = None) -> Approval:
        self._require_initialized()
        artifact = self.store.artifacts.get(artifact_id, version)
        approval = Approval(id=f"approval-{uuid4().hex[:12]}", artifact_id=artifact_id, version=artifact.metadata.version, decision="approved", reviewer=reviewer, note=note)
        self.store.save_approval(approval)
        self.store.artifacts.update_status(artifact_id, ArtifactStatus.APPROVED, artifact.metadata.version)
        self.store.artifacts.attach_approval(artifact_id, approval.id, artifact.metadata.version)
        state = self.store.load_state()
        state.pending_approvals = [item for item in state.pending_approvals if item != artifact_id]
        self.store.save_state(state)
        self.store.append_event("ARTIFACT_APPROVED", artifact_id=artifact_id, details={"version": artifact.metadata.version, "approval_id": approval.id})
        return approval

    def request_changes(self, artifact_id: str, note: str | None = None, reviewer: str = "user", version: int | None = None) -> Approval:
        self._require_initialized()
        artifact = self.store.artifacts.get(artifact_id, version)
        approval = Approval(id=f"approval-{uuid4().hex[:12]}", artifact_id=artifact_id, version=artifact.metadata.version, decision="changes_requested", reviewer=reviewer, note=note)
        self.store.save_approval(approval)
        self.store.artifacts.update_status(artifact_id, ArtifactStatus.CHANGES_REQUESTED, artifact.metadata.version)
        self.store.artifacts.attach_approval(artifact_id, approval.id, artifact.metadata.version)
        self.store.append_event("CHANGES_REQUESTED", artifact_id=artifact_id, details={"version": artifact.metadata.version, "approval_id": approval.id, "note": note})
        return approval

    def add_comment(self, artifact_id: str, text: str, *, author: str = "user", location: dict[str, Any] | None = None) -> Comment:
        self._require_initialized()
        artifact = self.store.artifacts.get(artifact_id)
        comment = Comment(id=f"comment-{uuid4().hex[:12]}", artifact_id=artifact_id, text=text, author=author, location=location)
        self.store.save_comment(comment)
        artifact.metadata.comments.append(comment.id)
        atomic_write(self.store.artifacts._metadata_path(artifact_id, artifact.metadata.version), artifact.metadata.model_dump_json(indent=2) + "\n")
        self.store.append_event("COMMENT_ADDED", artifact_id=artifact_id, details={"comment_id": comment.id})
        return comment

    def retry(self, artifact_id: str, instruction: str | None = None):
        self._require_initialized()
        current = self.store.artifacts.get(artifact_id)
        agent_id = current.metadata.generated_by.agent
        if agent_id == "runtime":
            raise ValueError("deterministic inspection artifacts do not support agent retry")
        comments = self.store.list_comments(artifact_id)
        inputs: dict[str, Any] = {artifact_id: current.content}
        for reference in current.metadata.inputs:
            try:
                inputs[reference.logical_id] = self.store.artifacts.get(reference.logical_id, reference.version).content
            except FileNotFoundError:
                continue
        values, generated_by = self._execute_agent(agent_id, [artifact_id], inputs, comments, instruction)
        artifact = self.store.artifacts.save(artifact_id, current.metadata.type, values[artifact_id], generated_by=generated_by, inputs=current.metadata.inputs, requirements=current.metadata.requirements, parent_version=current.metadata.version)
        self.store.artifacts.update_status(artifact_id, ArtifactStatus.SUPERSEDED, current.metadata.version)
        state = self.store.load_state()
        for step in self.workflow().steps:
            if artifact_id in step.inputs and step.type == "human-approval":
                state.step_states[step.id] = StepStatus.PENDING
                state.workflow_status = WorkflowStatus.PAUSED
        self.store.save_state(state)
        self.store.append_event("ARTIFACT_RETRIED", artifact_id=artifact_id, details={"version": artifact.metadata.version, "parent_version": current.metadata.version, "instruction": instruction})
        return artifact

    def dependencies(self, requirement_id: str) -> list[str]:
        return self.store.load_dependency_graph().requirements.get(requirement_id, [])
