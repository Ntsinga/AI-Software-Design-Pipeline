"""Output validators for `ProviderBackedAgent`.

Each validator is a callable `(values: dict, inputs: dict) -> list[str]`.
An empty list means the output is valid; a non-empty list of error strings
triggers a corrective retry (the errors are fed back to the model as its
next revision_instruction). Same feedback shape as the Mermaid tool-call
error the model already knows how to react to, but for cross-artifact
structural rules the tools themselves can't see.
"""

from __future__ import annotations

from typing import Any


def no_raw_ids_rendered_in_html(values: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    """`entity_id`/`workflow_id` are internal grouping metadata for the
    review workspace's own navigation -- never user-facing product copy.
    Observed live: the model rendered "Annual Plans Register
    (ia_annual_plan)" literally as an <h1>. Telling it these are
    metadata-only in the prompt wasn't enough on its own -- same "trust
    but verify" pattern as the other mockup validators.

    Handles both generation shapes: the normal mockups step (values has
    both mockup-spec and the full mockup-pages list) and a single-screen
    retry (values has one mockup-page-patch; mockup-spec is an upstream
    input instead, since it isn't being regenerated).
    """
    spec = values.get("mockup-spec") if isinstance(values.get("mockup-spec"), dict) else inputs.get("mockup-spec")
    if not isinstance(spec, dict):
        return []
    ids_by_screen: dict[str, set[str]] = {}
    for screen in spec.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        ids = {value for value in (screen.get("entity_id"), screen.get("workflow_id")) if value and value != "__landing__"}
        if ids:
            ids_by_screen[screen.get("id", "")] = ids
    if not ids_by_screen:
        return []

    pages: list[dict[str, Any]]
    if isinstance(values.get("mockup-pages"), list):
        pages = [page for page in values["mockup-pages"] if isinstance(page, dict)]
    elif isinstance(values.get("mockup-page-patch"), dict):
        pages = [values["mockup-page-patch"]]
    else:
        return []

    errors: list[str] = []
    for page in pages:
        screen_id = page.get("screen_id")
        html = page.get("html") or ""
        for raw_id in ids_by_screen.get(screen_id, ()):
            if raw_id in html:
                errors.append(f"screen '{screen_id}' renders the internal id '{raw_id}' as visible text in its HTML -- entity_id/workflow_id are metadata only, never user-facing copy; remove it from the title/heading/label and write a natural human-facing title instead")
    return errors


def data_model_relationships_reference_known_entities(values: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    """Every `data-model.relationships[*].from_entity`/`to_entity` must
    exactly match a `data-model.entities[*].name` in the same output --
    catches a typo'd or renamed entity reference the model didn't
    propagate everywhere. Same corrective-retry pattern as the mockup
    validators; only inspects `values`, so it's a no-op on any call that
    doesn't declare `data-model` as an output (safe to wire unconditionally
    for architecture-agent, which now serves two different steps)."""
    data_model = values.get("data-model")
    if not isinstance(data_model, dict):
        return []
    entity_names = {entity["name"] for entity in data_model.get("entities") or [] if isinstance(entity, dict) and entity.get("name")}
    errors: list[str] = []
    for relationship in data_model.get("relationships") or []:
        if not isinstance(relationship, dict):
            continue
        for endpoint_field in ("from_entity", "to_entity"):
            endpoint = relationship.get(endpoint_field)
            if endpoint and endpoint not in entity_names:
                errors.append(f"relationship {endpoint_field} '{endpoint}' does not match any data-model.entities[].name ({sorted(entity_names)}); copy the exact entity name verbatim, do not invent or abbreviate it")
    return errors


def entity_crud_coverage(values: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    """`mockup-spec.screens[*].entity_id` must be verbatim names from
    `system-model.entities`, and every entity must have at least TWO
    screens carrying its entity_id -- a list/register screen and a
    detail/edit/form screen, per the "ENTITY CRUD COVERAGE" agent
    constraint. There's no separate field distinguishing screen kind; two
    distinct screen ids sharing one entity_id is the signal.

    Same "trust but verify with retry" pattern as `workflow_id_coverage`:
    prompt alone reliably under-covers on real projects -- observed live
    producing exactly one screen per entity (a single combined view)
    instead of the promised list + detail/form pair.
    """
    spec = values.get("mockup-spec")
    if not isinstance(spec, dict):
        return []
    # Prefer the structured data-model's entity names once a project has
    # one -- it's the authoritative, directly-editable source of truth.
    # Fall back to system-model.entities (a flat name list) for older
    # projects that predate data-model.
    data_model = inputs.get("data-model")
    if isinstance(data_model, dict) and data_model.get("entities"):
        entity_names = [entity["name"] for entity in data_model.get("entities") or [] if isinstance(entity, dict) and entity.get("name")]
    else:
        system_model = inputs.get("system-model")
        entity_names = [name for name in (system_model.get("entities") or []) if isinstance(name, str) and name] if isinstance(system_model, dict) else []
    if not entity_names:
        return []
    allowed = set(entity_names)
    screens_by_entity: dict[str, list[str]] = {}
    errors: list[str] = []
    for screen in spec.get("screens") or []:
        entity_id = screen.get("entity_id") if isinstance(screen, dict) else None
        if not entity_id:
            continue  # Screens without entity_id are pure workflow/landing screens, not a coverage error.
        if entity_id not in allowed:
            errors.append(f"screen '{screen.get('id')}' has entity_id '{entity_id}' which is not one of system-model.entities ({entity_names}); copy the exact entity name verbatim, do not invent slugs")
            continue
        screens_by_entity.setdefault(entity_id, []).append(screen.get("id", "?"))
    missing = [name for name in entity_names if name not in screens_by_entity]
    if missing:
        errors.append(f"mockup-spec.screens is missing CRUD coverage entirely for these entities: {missing} -- every system-model.entities name must appear as entity_id on at least two screens (a list/register screen and a detail/edit/form screen)")
    single_screen_only = {name: ids for name, ids in screens_by_entity.items() if len(ids) < 2}
    if single_screen_only:
        detail = "; ".join(f"{name} (only screen '{ids[0]}')" for name, ids in single_screen_only.items())
        errors.append(f"these entities have only ONE screen when CRUD requires at least two (a list/register screen AND a separate detail/edit/form screen): {detail} -- add the missing screen for each")
    return errors


def workflow_id_coverage(values: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    """`mockup-spec.screens[*].workflow_id` must be verbatim ids from
    `architecture-model.workflows[*].id` (or '__landing__'). Dedicated entity
    CRUD screens (with `entity_id` and empty/omitted `workflow_id`) are
    permitted so they group cleanly under Entity navigation.

    Prompt-only enforcement of this rule failed reliably in live testing --
    the model invented shorter slugs (e.g. "wf_planning") or used workflow
    *names* as ids, and skipped multiple workflows. Validating and
    retrying with a specific error list is the only reliable fix.
    """
    spec = values.get("mockup-spec")
    if not isinstance(spec, dict):
        return []
    architecture = inputs.get("architecture-model")
    if not isinstance(architecture, dict):
        return []
    architecture_ids = [workflow["id"] for workflow in architecture.get("workflows") or [] if isinstance(workflow, dict) and workflow.get("id")]
    if not architecture_ids:
        return []
    allowed = set(architecture_ids) | {"__landing__"}
    seen: set[str] = set()
    errors: list[str] = []
    for screen in spec.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        workflow_id = screen.get("workflow_id")
        entity_id = screen.get("entity_id")
        if not workflow_id and entity_id:
            continue
        if not workflow_id and not entity_id:
            errors.append(f"screen '{screen.get('id', '?')}' is missing both workflow_id and entity_id -- every screen must carry either a workflow_id ({sorted(allowed)}) or an entity_id for CRUD screens")
            continue
        if workflow_id not in allowed:
            errors.append(f"screen '{screen.get('id')}' has workflow_id '{workflow_id}' which is not one of architecture-model.workflows[].id ({architecture_ids}) or '__landing__'; copy the exact id string, do not invent slugs or use workflow names")
            continue
        if workflow_id != "__landing__":
            seen.add(workflow_id)
    missing = [workflow_id for workflow_id in architecture_ids if workflow_id not in seen]
    if missing:
        errors.append(f"mockup-spec.screens is missing coverage for these architecture workflow_ids: {missing} -- every architecture-model.workflows[].id must appear in at least one screen")
    return errors
