"""Plan-first, multi-screen mockup change orchestration.

A user writes a free-text instruction describing changes across several mockup
screens (e.g. "add a 3-screen checkout flow reachable from the product list").
The system:

1.  Plans: one structured-output LLM call turns the instruction into an ordered
    list of narrow operations (retry_screen, add_screen, split_screen) with
    concrete arguments.
2.  Confirms: the plan is returned to the user for review before anything runs.
3.  Executes: each step dispatches to the existing DesignRuntime method.
    $step_N placeholders are resolved from real results as each step completes.
    Fail-stop on any error.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger("design_pipeline.mockup_chat")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class MockupChatStep(BaseModel):
    """One planned operation in a mockup-chat plan."""
    operation: Literal["retry_screen", "add_screen", "split_screen"]
    description: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)


class MockupChatPlan(BaseModel):
    """The full plan returned by the LLM planner."""
    summary: str = ""
    steps: list[MockupChatStep] = Field(default_factory=list)


class StepResult(BaseModel):
    """Live execution status of one plan step."""
    index: int
    description: str = ""
    operation: str = ""
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    error: str | None = None
    new_screen_id: str | None = None


class MockupChatSession(BaseModel):
    """The full session object returned to the frontend on every poll."""
    session_id: str
    project_id: str
    instruction: str
    plan: MockupChatPlan
    status: Literal["planned", "executing", "completed", "failed"] = "planned"
    steps: list[StepResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Session store (in-memory, transient)
# ---------------------------------------------------------------------------

class MockupChatSessionStore:
    """Thread-safe, in-memory session store.  Sessions are transient -- lost
    on server restart.  That's intentional: the changes themselves are durably
    committed as artifact versions; the session is only orchestration state."""

    def __init__(self) -> None:
        self._sessions: dict[str, MockupChatSession] = {}
        self._lock = threading.Lock()

    def save(self, session: MockupChatSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> MockupChatSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def for_project(self, project_id: str) -> list[MockupChatSession]:
        with self._lock:
            return [s for s in self._sessions.values() if s.project_id == project_id]


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------

_STEP_REF_RE = re.compile(r"^\$step_(\d+)$")

_OPERATIONS_THAT_PRODUCE_SCREENS = {"add_screen", "split_screen"}


def validate_plan(plan: MockupChatPlan, existing_screen_ids: set[str]) -> list[str]:
    """Return validation errors (empty list = valid)."""
    errors: list[str] = []
    allowed_ops = {"retry_screen", "add_screen", "split_screen"}

    for i, step in enumerate(plan.steps, start=1):
        if step.operation not in allowed_ops:
            errors.append(f"step {i}: unknown operation '{step.operation}'")
            continue

        args = step.arguments

        # Screen-id existence checks for operations that target an existing screen.
        if step.operation in ("retry_screen", "split_screen"):
            screen_id = args.get("screen_id")
            if not screen_id:
                errors.append(f"step {i}: {step.operation} requires 'screen_id'")
            elif screen_id not in existing_screen_ids:
                errors.append(f"step {i}: screen_id '{screen_id}' does not exist")

        if step.operation == "add_screen":
            if not args.get("description"):
                errors.append(f"step {i}: add_screen requires 'description'")

        if step.operation == "split_screen":
            if not args.get("extract_description"):
                errors.append(f"step {i}: split_screen requires 'extract_description'")

        # $step_N reference validation.
        for key, value in args.items():
            if not isinstance(value, str):
                continue
            match = _STEP_REF_RE.match(value)
            if match is None:
                continue
            ref_index = int(match.group(1))
            if ref_index < 1 or ref_index > len(plan.steps):
                errors.append(f"step {i}: reference '{value}' is out of range (plan has {len(plan.steps)} steps)")
            elif ref_index >= i:
                errors.append(f"step {i}: reference '{value}' is a forward or self reference")
            elif plan.steps[ref_index - 1].operation not in _OPERATIONS_THAT_PRODUCE_SCREENS:
                errors.append(f"step {i}: reference '{value}' points to a {plan.steps[ref_index - 1].operation} step, which does not produce a new screen")

    return errors


# ---------------------------------------------------------------------------
# Planning (one structured-output LLM call)
# ---------------------------------------------------------------------------

_PLAN_SYSTEM_PROMPT = """\
You are a mockup orchestration planner.  Given a description of the current \
mockup screens and a user instruction, produce a plan -- an ordered list of \
operations that carry out the instruction using ONLY these three operations:

1. retry_screen  -- regenerate one existing screen's HTML.
   Arguments: {"screen_id": "<existing id>", "instruction": "<what to change>"}

2. add_screen    -- add a brand-new screen to the mockup set.
   Arguments: {"description": "<what the new screen shows>", \
"link_from_screen_id": "<existing id or $step_N reference, optional>"}

3. split_screen  -- extract part of an existing screen into a new linked screen.
   Arguments: {"screen_id": "<existing id>", \
"extract_description": "<what to extract>"}

Rules:
- Order matters: steps execute sequentially, each seeing the result of previous steps.
- To reference a screen that an EARLIER step will create, use "$step_N" \
(1-indexed) as the value of link_from_screen_id.  Only add_screen and \
split_screen produce new screens; retry_screen does not.
- Only reference screen IDs that already exist or will be created by an earlier step.
- Each step should be the smallest useful unit of work.
- Produce a short summary of the whole plan.

Return your answer as mockup-chat-plan: {"summary": "...", "steps": [...]}
"""


def plan_mockup_changes(runtime: Any, instruction: str) -> MockupChatSession:
    """Send the instruction + current mockup state to the live provider and
    return a validated MockupChatSession with status='planned'."""
    from .provider_config import load_provider_settings
    from .providers import ProviderRequest, create_model_provider

    runtime._require_initialized()

    try:
        spec_artifact = runtime.store.artifacts.get("mockup-spec")
    except FileNotFoundError:
        raise ValueError("generate mockups before using mockup chat")
    try:
        runtime.store.artifacts.get("mockup-pages")
    except FileNotFoundError:
        raise ValueError("generate mockups before using mockup chat")

    screens = spec_artifact.content.get("screens", [])
    existing_ids = {s["id"] for s in screens}

    screen_summary = json.dumps(
        [{"id": s["id"], "name": s.get("name", ""), "purpose": s.get("purpose", "")} for s in screens],
        indent=2,
    )

    settings = load_provider_settings(runtime.root, database_url=runtime._database_url)
    if settings.provider == "stub":
        raise ValueError("mockup chat requires a live provider (not stub)")
    provider = create_model_provider(settings)

    user_prompt = json.dumps({
        "current_screens": json.loads(screen_summary),
        "instruction": instruction,
    })

    response_object_keys = {
        "mockup-chat-plan": {
            "kind": "object",
            "schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "operation": {"type": "string"},
                                "description": {"type": "string"},
                                "arguments": {"type": "object", "properties": {}, "required": []},
                            },
                            "required": ["operation", "description", "arguments"],
                        },
                    },
                },
                "required": ["summary", "steps"],
            },
        }
    }

    request = ProviderRequest(
        system_prompt=_PLAN_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.0,
        response_object_keys=response_object_keys,
    )
    response = provider.generate(request)

    # Parse the plan from the model's JSON response.
    try:
        raw = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"planner returned invalid JSON: {exc}") from exc

    plan_data = raw.get("mockup-chat-plan", raw)
    plan = MockupChatPlan(**plan_data)

    if not plan.steps:
        raise ValueError("planner produced an empty plan -- try rephrasing")

    validation_errors = validate_plan(plan, existing_ids)
    if validation_errors:
        raise ValueError(f"planner produced an invalid plan: {'; '.join(validation_errors)}")

    session_id = uuid4().hex[:12]
    session = MockupChatSession(
        session_id=session_id,
        project_id=runtime.store.paths.project_id,
        instruction=instruction,
        plan=plan,
        status="planned",
        steps=[
            StepResult(index=i, description=step.description, operation=step.operation)
            for i, step in enumerate(plan.steps)
        ],
    )
    return session


# ---------------------------------------------------------------------------
# Execution (background thread, deterministic dispatch)
# ---------------------------------------------------------------------------

def _resolve_references(arguments: dict[str, Any], reference_map: dict[str, str]) -> dict[str, Any]:
    """Replace $step_N placeholders in argument values with the actual screen
    IDs recorded after earlier steps completed."""
    resolved = {}
    for key, value in arguments.items():
        if isinstance(value, str) and _STEP_REF_RE.match(value):
            if value not in reference_map:
                raise ValueError(f"unresolved reference '{value}' -- the referenced step may have failed or not produced a new screen")
            resolved[key] = reference_map[value]
        else:
            resolved[key] = value
    return resolved


def _detect_new_screen_id(before_ids: set[str], runtime: Any) -> str | None:
    """Diff mockup-spec screen IDs before/after a mutation to find the newly
    added screen.  Returns None if no new screen was detected (shouldn't
    happen on a successful add/split, but don't crash)."""
    try:
        spec = runtime.store.artifacts.get("mockup-spec")
        after_ids = {s["id"] for s in spec.content.get("screens", [])}
        new_ids = after_ids - before_ids
        return new_ids.pop() if new_ids else None
    except Exception:
        return None


def execute_mockup_chat(runtime: Any, session: MockupChatSession) -> None:
    """Run every step in the plan sequentially, dispatching to the existing
    DesignRuntime methods.  Fail-stop on the first error.

    Designed to run in a background thread -- mutates `session` in place so
    the poll endpoint can read live progress."""
    session.status = "executing"
    reference_map: dict[str, str] = {}

    for i, step_def in enumerate(session.plan.steps):
        step_result = session.steps[i]
        step_result.status = "running"

        try:
            args = _resolve_references(step_def.arguments, reference_map)
        except ValueError as exc:
            step_result.status = "failed"
            step_result.error = str(exc)
            session.status = "failed"
            logger.error("Mockup chat step %d failed (reference resolution): %s", i + 1, exc)
            return

        # Snapshot screen IDs before the mutation (for detecting new screens).
        before_ids: set[str] = set()
        if step_def.operation in _OPERATIONS_THAT_PRODUCE_SCREENS:
            try:
                spec = runtime.store.artifacts.get("mockup-spec")
                before_ids = {s["id"] for s in spec.content.get("screens", [])}
            except FileNotFoundError:
                pass

        try:
            if step_def.operation == "retry_screen":
                runtime.retry_screen(args["screen_id"], args.get("instruction"))
            elif step_def.operation == "add_screen":
                runtime.add_mockup_screen(args["description"], args.get("link_from_screen_id"))
            elif step_def.operation == "split_screen":
                runtime.split_mockup_screen(args["screen_id"], args["extract_description"])
            else:
                raise ValueError(f"unknown operation '{step_def.operation}'")
        except Exception as exc:
            step_result.status = "failed"
            step_result.error = str(exc)
            session.status = "failed"
            logger.exception("Mockup chat step %d (%s) failed", i + 1, step_def.operation)
            return

        # Record the new screen ID for add/split so later $step_N refs resolve.
        if step_def.operation in _OPERATIONS_THAT_PRODUCE_SCREENS:
            new_id = _detect_new_screen_id(before_ids, runtime)
            if new_id:
                reference_map[f"$step_{i + 1}"] = new_id
                step_result.new_screen_id = new_id

        step_result.status = "completed"

    session.status = "completed"
    try:
        runtime.store.append_event("MOCKUP_CHAT_EXECUTED", artifact_id="mockup-pages", details={
            "session_id": session.session_id,
            "instruction": session.instruction,
            "steps_completed": sum(1 for s in session.steps if s.status == "completed"),
            "steps_total": len(session.steps),
        })
    except Exception:
        logger.exception("Failed to append MOCKUP_CHAT_EXECUTED event")
