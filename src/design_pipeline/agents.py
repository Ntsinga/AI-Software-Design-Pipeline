"""Provider-neutral agent contracts and deterministic MVP agents."""

from __future__ import annotations

import json
import re
import types
from pathlib import Path
from typing import Any, Protocol, Union, get_args, get_origin

import yaml

from pydantic import TypeAdapter

from .documents import DocumentReader
from .models import AgentDefinition, BusinessModel, Comment, DataModel, Handoff, MockupPage, MockupScreenAddition, MockupSpec, SolutionModel, SystemModel
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
    # Used only by DesignRuntime.add_mockup_screen -- adds exactly one new
    # screen (plus an optional patch to the one existing screen that should
    # now link to it) without touching or resending the rest of the mockup
    # set. Never a declared workflow-step output.
    "mockup-screen-addition": MockupScreenAddition,
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
            "Its keys must exactly include every declared output, spelled and punctuated exactly as given in "
            "declared_outputs -- including any hyphens (e.g. the key `mockup-page-patch` is the JSON key "
            "\"mockup-page-patch\", not \"mockup_page_patch\" or \"mockupPagePatch\"). Do not use Markdown fences."
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
        # Ask the provider to *enforce* the declared top-level keys via its
        # native structured-output support, not just describe them in the
        # prompt -- prompt-only guidance is what let a live model rename,
        # wrap, or flatten `mockup-page-patch` and still pass everything
        # else. Only offered when this call has no tools: Gemini's
        # `responseSchema` and function-calling tools are mutually
        # exclusive in the same request, and every tool-using agent here
        # (e.g. architecture-agent's mermaid.render) already has
        # ProviderBackedAgent._recover_declared_keys as its safety net.
        response_object_keys = None if tool_specs else {output: self._output_shape(output) for output in outputs}
        request = ProviderRequest(system_prompt=system_prompt, user_prompt=json.dumps(requested, default=str), temperature=0.0, tools=tool_specs, response_object_keys=response_object_keys)
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
            request = ProviderRequest(system_prompt=system_prompt, user_prompt=request.user_prompt, temperature=0.0, tools=tool_specs, history=response.history, tool_results=tool_results, response_object_keys=response_object_keys)
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
        values = self._recover_declared_keys(values, outputs)
        missing = [output for output in outputs if output not in values]
        if missing:
            # Include what the model actually returned -- otherwise this
            # error is a dead end: there's no way to tell, from "did not
            # produce X", whether the model wrapped X under another key,
            # nested it, or omitted it outright. Truncate defensively;
            # a full mockup-pages array can be very large.
            received = json.dumps(values, default=str)
            if len(received) > 800:
                received = received[:800] + "...(truncated)"
            raise ValueError(f"{self.provider.name} did not produce declared output(s): {', '.join(missing)} -- it returned these top-level keys instead: {sorted(values)} (raw: {received})")
        unexpected = sorted(set(values) - set(outputs))
        if unexpected:
            raise ValueError(f"{self.provider.name} returned undeclared output(s): {', '.join(unexpected)}")
        return {output: values[output] for output in outputs}

    _PRIMITIVE_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}

    @classmethod
    def _field_schema(cls, annotation: Any) -> dict[str, Any] | None:
        """Recursively build a provider-neutral, lowercase-typed JSON-Schema
        dict (`{"type": "object", "properties": {...}, "required": [...]}`,
        `{"type": "array", "items": ...}`, or a bare `{"type": "string"}`
        etc.) for one field's type annotation -- descending into nested
        pydantic models, lists, and `Optional[X]`/`X | None` -- or `None`
        when the type is too complex to safely translate (a union of
        several real types, a bare `Any`/`dict`, ...), signalling the
        caller to fall back to an unconstrained object for that spot.

        Never emits `$defs`/`$ref`: everything is inlined in place instead,
        because Gemini's structured-output schema subset doesn't reliably
        support them. Going only one level deep used to be enough for
        mockup-page-patch (screen_id/html, both bare strings) but wasn't
        for mockup-screen-addition (screen/page/updated_source_page, each
        itself a nested model) -- confirmed live, Gemini returned an empty
        `{}` for it the same way it once did for mockup-page-patch, because
        depth 1 left those nested objects just as unconstrained as no
        schema at all. This recurses all the way down instead.
        """
        if annotation in cls._PRIMITIVE_JSON_TYPES:
            return {"type": cls._PRIMITIVE_JSON_TYPES[annotation]}
        origin = get_origin(annotation)
        if origin is list:
            args = get_args(annotation)
            item_schema = cls._field_schema(args[0]) if args else None
            return {"type": "array", "items": item_schema} if item_schema is not None else None
        if origin is Union or origin is types.UnionType:
            # Optional[X] / X | None: translate the one real type, treating
            # the field as optional at the parent level (handled by the
            # caller via `field.is_required()`) rather than here. A union
            # of more than one real type has no safe single translation.
            real_args = [arg for arg in get_args(annotation) if arg is not type(None)]
            return cls._field_schema(real_args[0]) if len(real_args) == 1 else None
        model_fields = getattr(annotation, "model_fields", None)
        if model_fields:
            properties: dict[str, Any] = {}
            required: list[str] = []
            for name, field in model_fields.items():
                sub_schema = cls._field_schema(field.annotation)
                if sub_schema is None:
                    return None  # one untranslatable field forfeits the whole nested object
                properties[name] = sub_schema
                if field.is_required():
                    required.append(name)
            return {"type": "object", "properties": properties, "required": required}
        return None

    @classmethod
    def _output_shape(cls, output: str) -> dict[str, Any]:
        """The provider-neutral shape hint for one declared output's
        native-structured-output envelope -- see
        `ProviderRequest.response_object_keys` for the full contract."""
        schema_type = OUTPUT_SCHEMAS.get(output)
        kind = "object"
        model = schema_type
        if get_origin(schema_type) is list:
            kind, model = "array", get_args(schema_type)[0]
        return {"kind": kind, "schema": cls._field_schema(model) if model is not None else None}

    @staticmethod
    def _recover_declared_keys(values: dict[str, Any], outputs: list[str]) -> dict[str, Any]:
        """Remap keys a model returned under a near-miss of a declared
        output's name, instead of failing the whole generation over it.

        Hyphenated names like `mockup-page-patch` aren't valid identifiers
        in most languages, so a model (small/cheap ones especially, e.g.
        Gemini flash-lite on `retry_screen`) sometimes silently renames one
        to `mockup_page_patch` or `mockupPagePatch` while otherwise
        producing exactly the content asked for. Match case/punctuation-
        insensitively first; if that still leaves a single-output request
        unmatched and the model returned exactly one key under any name,
        trust that key was meant for the one declared output -- covers a
        model inventing an unrelated name entirely (e.g. "patch", "result").
        """
        def normalize(key: str) -> str:
            return re.sub(r"[^a-z0-9]", "", key.lower())

        remapped = dict(values)
        normalized_lookup = {normalize(key): key for key in remapped}
        for output in outputs:
            if output in remapped:
                continue
            actual_key = normalized_lookup.get(normalize(output))
            if actual_key is not None and actual_key not in outputs:
                remapped[output] = remapped.pop(actual_key)
                normalized_lookup = {normalize(key): key for key in remapped}
        # A chattier model sometimes wraps the whole answer under an
        # explanatory key instead of naming the declared outputs at the top
        # level at all -- e.g. {"response": {"mockup-page-patch": {...}}} or
        # {"result": {...}}. Look one level down inside any remaining
        # dict-valued key for a still-missing output's name (exact or
        # normalized) and hoist it out; drop the now-empty wrapper key so it
        # doesn't get flagged as an unexpected output afterward.
        for wrapper_key in list(remapped):
            if wrapper_key in outputs or not isinstance(remapped[wrapper_key], dict):
                continue
            nested = remapped[wrapper_key]
            nested_lookup = {normalize(key): key for key in nested}
            hoisted_any = False
            for output in outputs:
                if output in remapped:
                    continue
                nested_key = output if output in nested else nested_lookup.get(normalize(output))
                if nested_key is not None:
                    remapped[output] = nested[nested_key]
                    hoisted_any = True
            if hoisted_any:
                remapped.pop(wrapper_key, None)
        if len(outputs) == 1 and outputs[0] not in remapped and remapped:
            # Last resort for a single-output request (retry_screen's only
            # caller). Every other recovery above has already had its
            # chance, so whatever's left in `remapped` must be the answer --
            # there's nothing else it could be. Two shapes seen live:
            #  - exactly one leftover key wrapping the real value, e.g.
            #    {"patch": {"screen_id": ..., "html": ...}} -- unwrap it.
            #  - the declared output's own schema fields flattened at the
            #    top level with no wrapper key at all, e.g.
            #    {"screen_id": ..., "html": ...} instead of
            #    {"mockup-page-patch": {"screen_id": ..., "html": ...}}
            #    (confirmed live: Gemini flash-lite did exactly this) --
            #    the whole remaining dict IS the value.
            remapped = {outputs[0]: next(iter(remapped.values())) if len(remapped) == 1 else remapped}
        return remapped

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
