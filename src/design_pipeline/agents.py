"""Provider-neutral agent contracts and deterministic MVP agents."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

import yaml

from .documents import DocumentReader
from .models import AgentDefinition, Comment, Handoff
from .providers import ModelProvider, ProviderRequest


class Agent(Protocol):
    definition: AgentDefinition

    def run(self, outputs: list[str], inputs: dict[str, Any], comments: list[Comment] | None = None, instruction: str | None = None) -> dict[str, Any]:
        ...


class AgentLoader:
    def __init__(self, directory: Path):
        self.directory = directory

    def load(self, agent_id: str) -> AgentDefinition:
        path = self.directory / f"{agent_id.removesuffix('-agent')}.yaml"
        if not path.exists():
            path = self.directory / f"{agent_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"agent definition not found: {agent_id}")
        return AgentDefinition.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def load_all(self) -> dict[str, AgentDefinition]:
        return {self.load(path.stem).id: self.load(path.stem) for path in self.directory.glob("*.yaml")}


class DeterministicAgent:
    """Fixture-backed agent used until a live provider adapter is configured."""

    def __init__(self, definition: AgentDefinition, project_root: Path):
        self.definition = definition
        self.project_root = project_root

    def run(self, outputs: list[str], inputs: dict[str, Any], comments: list[Comment] | None = None, instruction: str | None = None) -> dict[str, Any]:
        comments = comments or []
        if self.definition.id == "requirements-agent":
            return self._requirements(outputs, inputs)
        if self.definition.id == "architecture-agent":
            return self._architecture(outputs, inputs, comments, instruction)
        if self.definition.id == "ux-agent":
            return self._ux(outputs, inputs, comments, instruction)
        raise ValueError(f"no deterministic implementation for {self.definition.id}")

    def _requirements(self, outputs: list[str], inputs: dict[str, Any]) -> dict[str, Any]:
        brd = inputs.get("brd")
        if not brd:
            source = DocumentReader(self.project_root).read_brd()
            brd = source.content if source else (
                "# Business Requirements Document\n\n"
                "## BR-001 — Manage design projects\n"
                "The user must be able to initialize a project and inspect its design artifacts.\n\n"
                "## BR-002 — Review generated designs\n"
                "The user must be able to approve generated artifacts or request changes.\n"
            )
        requirements = sorted(set(re.findall(r"BR-\d{3,}", str(brd)))) or ["BR-001", "BR-002"]
        result: dict[str, Any] = {}
        for output in outputs:
            if output == "brd":
                result[output] = brd
            elif output == "business-model":
                result[output] = {
                    "actors": ["Project Owner", "Design Reviewer"],
                    "stakeholders": ["Project Team"],
                    "capabilities": ["Manage design projects", "Review design artifacts"],
                    "goals": ["Create a traceable design baseline", "Shorten review cycles"],
                    "processes": ["Initialize project", "Generate design", "Review artifact", "Request revision"],
                    "rules": ["Approved artifacts are not silently replaced", "Changes are recorded as structured data"],
                    "outcomes": ["Approved design baseline", "Actionable review feedback"],
                    "responsibilities": {"Project Owner": ["Provide requirements", "Approve artifacts"], "Design Reviewer": ["Review artifacts", "Request changes"]},
                    "external_organizations": [],
                    "events": ["Project initialized", "Artifact generated", "Artifact approved", "Changes requested"],
                }
            elif output == "solution-model":
                result[output] = {
                    "capabilities": ["Project initialization", "Workflow orchestration", "Artifact review"],
                    "application_boundaries": ["Design Pipeline Runtime", "Review Client"],
                    "components": ["CLI", "FastAPI API", "Workflow Engine", "Artifact Registry", "Stub Agents"],
                    "integrations": [],
                    "data_ownership": {"Design Pipeline Runtime": "Project artifacts and workflow state"},
                    "user_capabilities": ["Initialize", "Run", "Approve", "Comment", "Retry"],
                    "external_dependencies": [],
                    "security_concepts": ["Local project access"],
                    "constraints": ["Filesystem-backed persistence", "Provider-neutral agent contracts"],
                }
            elif output == "system-model":
                result[output] = {
                    "requirements": requirements,
                    "business_capabilities": ["Manage design projects", "Review design artifacts"],
                    "business_workflows": ["Initialize project", "Generate design", "Review artifact", "Request revision"],
                    "system_capabilities": ["Workflow orchestration", "Artifact versioning", "Approval gates", "Comment persistence"],
                    "entities": ["Project", "Artifact", "Comment", "Approval", "Workflow Step"],
                    "services": ["CLI", "FastAPI API", "Workflow Engine", "Artifact Registry"],
                    "screens": ["Project Status", "Artifact List", "Artifact Review"],
                    "integrations": [],
                    "permissions": {"Project Owner": ["read", "approve", "request_changes", "retry"], "Design Reviewer": ["read", "comment", "approve", "request_changes"]},
                    "traceability": {"BR-001": ["business-model", "solution-model", "project-inspection"], "BR-002": ["system-model", "architecture-model", "mockup-spec"]},
                }
        return result

    def _architecture(self, outputs: list[str], inputs: dict[str, Any], comments: list[Comment], instruction: str | None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        architecture = {
            "style": "modular monolith",
            "components": ["CLI", "FastAPI API", "Workflow Engine", "Artifact Registry", "Agent Runtime"],
            "data_store": "Git-friendly project filesystem",
            "boundaries": ["Runtime", "Persistence", "Adapters"],
            "rationale": "A small local runtime keeps orchestration deterministic while preserving provider and MCP extension points.",
            "feedback_applied": [comment.text for comment in comments],
            "instruction": instruction,
        }
        recommendations = {
            "recommended": ["system-context", "container-architecture", "artifact-lifecycle"],
            "not_yet_necessary": ["deployment", "detailed-sequence"],
            "reasons": {"system-context": "Shows users and the pipeline boundary.", "container-architecture": "Shows the MVP runtime responsibilities.", "artifact-lifecycle": "Makes approval and retry behavior explicit."},
        }
        for output in outputs:
            if output == "architecture-model":
                result[output] = architecture
            elif output == "diagram-recommendations":
                result[output] = recommendations
        return result

    def _ux(self, outputs: list[str], inputs: dict[str, Any], comments: list[Comment], instruction: str | None) -> dict[str, Any]:
        return {
            output: {
                "type": "interactive-mockup-specification",
                "screens": ["Project Status", "Artifact Review", "Version History"],
                "primary_flow": ["Project Status", "Artifact Review", "Approve or Request Changes", "Version History"],
                "synthetic_data": True,
                "feedback_applied": [comment.text for comment in comments],
                "instruction": instruction,
            }
            for output in outputs
        }


class ProviderBackedAgent:
    """Run a declarative agent through a selected provider and validate its JSON handoff."""

    def __init__(self, definition: AgentDefinition, provider: ModelProvider):
        self.definition = definition
        self.provider = provider

    def run(self, outputs: list[str], inputs: dict[str, Any], comments: list[Comment] | None = None, instruction: str | None = None) -> dict[str, Any]:
        requested = {
            "agent": self.definition.id,
            "objective": self.definition.description,
            "constraints": self.definition.constraints,
            "declared_outputs": outputs,
            "inputs": inputs,
            "review_comments": [comment.text for comment in comments or []],
            "revision_instruction": instruction,
        }
        system_prompt = (
            "You are a software-design pipeline agent. Return only one valid JSON object. "
            "Its keys must exactly include every declared output. Do not use Markdown fences."
        )
        response = self.provider.generate(
            ProviderRequest(system_prompt=system_prompt, user_prompt=json.dumps(requested, default=str), temperature=0.0)
        )
        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            values = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{self.provider.name} returned invalid JSON for {self.definition.id}: {exc.msg}") from exc
        if not isinstance(values, dict):
            raise ValueError(f"{self.provider.name} returned a non-object JSON result for {self.definition.id}")
        missing = [output for output in outputs if output not in values]
        if missing:
            raise ValueError(f"{self.provider.name} did not produce declared output(s): {', '.join(missing)}")
        unexpected = sorted(set(values) - set(outputs))
        if unexpected:
            raise ValueError(f"{self.provider.name} returned undeclared output(s): {', '.join(unexpected)}")
        return {output: values[output] for output in outputs}


def create_handoff(source_agent: str, target_agent: str, objective: str, inputs: dict[str, str], expected_outputs: list[str], *, changes: list[str] | None = None, constraints: list[str] | None = None, task_id: str = "task-pending") -> Handoff:
    return Handoff(task_id=task_id, source_agent=source_agent, target_agent=target_agent, objective=objective, inputs=inputs, expected_outputs=expected_outputs, changes=changes or [], constraints=constraints or [])
