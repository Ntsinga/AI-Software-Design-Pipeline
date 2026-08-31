"""Provider-neutral agent contracts and deterministic MVP agents."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

import yaml

from pydantic import TypeAdapter

from .documents import DocumentReader
from .models import AgentDefinition, BusinessModel, Comment, DataModel, Handoff, MockupPage, MockupSpec, SolutionModel, SystemModel
from .providers import ModelProvider, ProviderRequest
from .tools.base import Tool

# Outputs with a strict Pydantic contract elsewhere in the app (the review
# workspace's rendering code, in particular, expects these exact field
# names). Without this, a live model has no way to know the expected shape
# and reliably invents its own -- the deterministic stub agent below always
# matches these because it's hand-written to, but nothing previously told a
# live model what "system-model" etc. actually needs to contain. Values are
# anything `TypeAdapter` accepts, so a list-shaped output (`mockup-pages`)
# works the same as a single-object one.
OUTPUT_SCHEMAS: dict[str, Any] = {
    "business-model": BusinessModel,
    "solution-model": SolutionModel,
    "system-model": SystemModel,
    "data-model": DataModel,
    "mockup-spec": MockupSpec,
    "mockup-pages": list[MockupPage],
    # Used only by DesignRuntime.retry_screen -- a single-page shape (not
    # the list) so the model returns exactly one screen's HTML instead of
    # rewriting the whole set. Never a declared workflow-step output.
    "mockup-page-patch": MockupPage,
}


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
        diagrams = [{
            "name": "Container architecture",
            "diagram_type": "flowchart",
            "mermaid_source": "flowchart TD\n  CLI --> Runtime\n  API --> Runtime\n  Runtime --> Registry[(Artifact Registry)]",
            "valid": True,
            "detail": "deterministic fixture, not rendered",
        }]
        data_model = {
            "entities": [
                {"name": "artifact", "description": "One versioned pipeline artifact.", "fields": [{"name": "logical_id", "type": "string"}, {"name": "version", "type": "integer"}, {"name": "status", "type": "string"}]},
                {"name": "comment", "description": "A review comment attached to an artifact.", "fields": [{"name": "text", "type": "string"}, {"name": "author", "type": "string"}]},
            ],
            "relationships": [
                {"from_entity": "artifact", "to_entity": "comment", "cardinality": "one-to-many", "label": "has"},
            ],
        }
        for output in outputs:
            if output == "architecture-model":
                result[output] = architecture
            elif output == "diagram-recommendations":
                result[output] = recommendations
            elif output == "diagrams":
                result[output] = diagrams
            elif output == "data-model":
                result[output] = data_model
        return result

    def _ux(self, outputs: list[str], inputs: dict[str, Any], comments: list[Comment], instruction: str | None) -> dict[str, Any]:
        screens = [
            {"id": "project-status", "name": "Project Status", "purpose": "Overview of workflow progress.", "key_elements": ["Stat cards", "Artifact list"], "workflow_link": "Links into Artifact Review.", "workflow_id": "__landing__"},
            {"id": "artifact-review", "name": "Artifact Review", "purpose": "Inspect a single artifact and act on it.", "key_elements": ["Content preview", "Approve / request changes"], "workflow_link": "Approving advances to the next gate.", "workflow_id": "review"},
            {"id": "version-history", "name": "Version History", "purpose": "Browse prior versions of an artifact.", "key_elements": ["Version list", "Status badges"], "workflow_link": "Selecting a version reopens it in Artifact Review.", "workflow_id": "review"},
        ]
        result: dict[str, Any] = {}
        for output in outputs:
            if output == "mockup-spec":
                result[output] = {
                    "type": "interactive-mockup-specification",
                    "screens": screens,
                    "primary_flow": [screen["name"] for screen in screens] + ["Approve or Request Changes"],
                    "synthetic_data": True,
                    "feedback_applied": [comment.text for comment in comments],
                    "instruction": instruction,
                }
            elif output == "mockup-pages":
                result[output] = [
                    {"screen_id": screen["id"], "html": f"<!doctype html><html><body><h1>{screen['name']}</h1><p>Deterministic fixture, not a live render.</p></body></html>"}
                    for screen in screens
                ]
        return result


class ProviderBackedAgent:
    """Run a declarative agent through a selected provider and validate its JSON handoff.

    When `tools` are given, this drives a real multi-turn tool-calling loop:
    the model may call a tool instead of finishing, the tool actually runs,
    and its result is fed back for the model to react to -- bounded by
    `max_tool_iterations` so a model that never stops calling tools fails
    with a clear, retryable error instead of looping forever.
    """

    def __init__(self, definition: AgentDefinition, provider: ModelProvider, tools: list[Tool] | None = None, max_tool_iterations: int = 20, output_validators: list[Any] | None = None, max_validation_retries: int = 2):
        self.definition = definition
        self.provider = provider
        self.tools = tools or []
        self.max_tool_iterations = max_tool_iterations
        self._tools_by_name = {tool.spec.name: tool for tool in self.tools}
        # Output validators: callables `(values, inputs) -> list[str]`. A
        # non-empty list of error strings triggers a corrective retry that
        # feeds the errors back to the model as a follow-up user message.
        # Some rules (e.g. "every workflow_id must exist in
        # architecture-model.workflows[].id") can't be expressed as JSON
        # schema alone -- they depend on cross-artifact input values -- and
        # prompt guidance alone doesn't reliably enforce them.
        self.output_validators = list(output_validators or [])
        self.max_validation_retries = max_validation_retries
        # Every successful call made during the most recent `run()`, in
        # order: {"tool": name, "arguments": {...}, "result": {...}}. Models
        # sometimes re-transcribe a tool's result into their final JSON
        # answer instead of reusing it verbatim (renamed fields, or even
        # subtly altered content); callers that need the tool's own output
        # byte-for-byte -- e.g. a validated Mermaid diagram -- should use
        # this instead of trusting the model's restatement.
        self.last_tool_calls: list[dict[str, Any]] = []

    def run(self, outputs: list[str], inputs: dict[str, Any], comments: list[Comment] | None = None, instruction: str | None = None) -> dict[str, Any]:
        attempt_instruction = instruction
        for validation_attempt in range(self.max_validation_retries + 1):
            values = self._one_generation(outputs, inputs, comments, attempt_instruction)
            errors: list[str] = []
            for validator in self.output_validators:
                errors.extend(validator(values, inputs) or [])
            if not errors:
                return values
            if validation_attempt == self.max_validation_retries:
                raise ValueError(f"{self.provider.name} produced output for {self.definition.id} that failed validation after {self.max_validation_retries + 1} attempt(s): {'; '.join(errors)}")
            # Feed the errors back as a corrective instruction on the next
            # generation. Same pattern as the model reacting to a Mermaid
            # tool-call error, but for structural rules the tool can't see.
            attempt_instruction = ("Your previous answer failed validation. Fix these specific issues and try again -- these are hard requirements, not suggestions:\n- " + "\n- ".join(errors) + ("\nAlso keep addressing the original instruction: " + instruction if instruction else ""))
        raise AssertionError("unreachable")

    def _one_generation(self, outputs: list[str], inputs: dict[str, Any], comments: list[Comment] | None, instruction: str | None) -> dict[str, Any]:
        schemas = {output: TypeAdapter(OUTPUT_SCHEMAS[output]).json_schema() for output in outputs if output in OUTPUT_SCHEMAS}
        requested = {
            "agent": self.definition.id,
            "objective": self.definition.description,
            "constraints": self.definition.constraints,
            "declared_outputs": outputs,
            "output_schemas": schemas,
            "inputs": inputs,
            "review_comments": [self._format_comment(comment) for comment in comments or []],
            "revision_instruction": instruction,
        }
        system_prompt = (
            "You are a software-design pipeline agent. Return only one valid JSON object. "
            "Its keys must exactly include every declared output. Do not use Markdown fences."
        )
        if schemas:
            system_prompt += (
                " For every declared output that has an entry in output_schemas, your value for that key must "
                "conform exactly to the given JSON schema -- the same field names and structure, not your own "
                "interpretation of what that output should contain."
            )
        if self.tools:
            system_prompt += (
                " You have tools available -- call them as needed (for example, to validate and render "
                "any diagrams you produce) and react to their results before giving your final JSON answer. "
                "When your final answer includes data a tool already returned, reuse that data's field names "
                "and values exactly as the tool gave them -- do not rename fields or re-describe the result in "
                "your own words."
            )
        tool_specs = [tool.spec for tool in self.tools]
        request = ProviderRequest(system_prompt=system_prompt, user_prompt=json.dumps(requested, default=str), temperature=0.0, tools=tool_specs)
        response = self.provider.generate(request)

        self.last_tool_calls = []
        iterations = 1
        while not response.is_final:
            if iterations >= self.max_tool_iterations:
                raise ValueError(f"{self.provider.name} exceeded the tool-call limit ({self.max_tool_iterations}) for {self.definition.id} without a final answer")
            tool_results: dict[str, str] = {}
            for call in response.tool_calls:
                tool = self._tools_by_name.get(call.name)
                if tool is None:
                    tool_results[call.id] = json.dumps({"error": f"unknown tool: {call.name}"})
                    continue
                try:
                    result = tool.execute(**call.arguments)
                except Exception as exc:
                    result = {"error": str(exc)}
                else:
                    self.last_tool_calls.append({"tool": call.name, "arguments": call.arguments, "result": result})
                tool_results[call.id] = json.dumps(result, default=str)
            request = ProviderRequest(system_prompt=system_prompt, user_prompt=request.user_prompt, temperature=0.0, tools=tool_specs, history=response.history, tool_results=tool_results)
            response = self.provider.generate(request)
            iterations += 1

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

    @staticmethod
    def _format_comment(comment: Comment) -> str:
        """Prepend a comment's target when it has one, so the model knows
        what to change: a whole-screen comment reads as `[screen: xyz]
        <text>`, an element-scoped comment as `[screen: xyz element:
        button.btn-primary] <text>`, a diagram comment as `[diagram: ERD]
        <text>`. An unscoped comment (older projects) sends just the text.
        """
        location = comment.location or {}
        kind = location.get("kind")
        if kind == "element":
            return f"[screen: {location.get('screen_id', '?')} element: {location.get('selector', '?')}] {comment.text}"
        if kind == "screen":
            return f"[screen: {location.get('screen_id', '?')}] {comment.text}"
        if kind == "diagram":
            return f"[diagram: {location.get('diagram_name', '?')}] {comment.text}"
        return comment.text


def create_handoff(source_agent: str, target_agent: str, objective: str, inputs: dict[str, str], expected_outputs: list[str], *, changes: list[str] | None = None, constraints: list[str] | None = None, task_id: str = "task-pending") -> Handoff:
    return Handoff(task_id=task_id, source_agent=source_agent, target_agent=target_agent, objective=objective, inputs=inputs, expected_outputs=expected_outputs, changes=changes or [], constraints=constraints or [])
