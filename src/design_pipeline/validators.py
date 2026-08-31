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


def _hierarchy_chain_components(data_model: dict[str, Any] | None) -> list[set[str]]:
    """STRICT LINEAR chains of 3+ entities linked by one-to-many
    relationships -- e.g. a five-level fieldwork register
    (MajorProcess -> SubProcess -> RiskStatement -> TestObjective ->
    Procedure). These are expected to be managed through ONE shared
    nested/inline editor, not dedicated CRUD screens per level -- forcing
    2 screens on every level of a deep hierarchy contradicts a domain
    that explicitly describes adding an item at any level without
    navigating to a separate screen.

    Deliberately narrow: a member may have AT MOST ONE incoming one-to-
    many edge (one parent) and AT MOST ONE outgoing one-to-many edge (one
    child type). The moment an entity branches into two or more distinct
    child types (e.g. Procedure -> Workpaper AND Procedure -> AuditIssue),
    the chain stops there -- that branching entity and everything past it
    goes back to needing normal, independent CRUD coverage.

    A naive "any connected component of one-to-many edges" version of
    this over-exempted almost the entire data model in practice: real
    domains commonly have every entity transitively reachable from a
    handful of root entities (AuditPlan -> AuditProject -> ... -> a deep
    chain), so a loose connected-components check swept up standalone,
    CRUD-worthy entities like AuditPlan/AuditProject/AuditUnit right
    alongside the fieldwork register they merely happen to be upstream
    of. This directed, branch-sensitive walk fixes that.
    """
    if not isinstance(data_model, dict):
        return []
    children: dict[str, list[str]] = {}
    parents: dict[str, list[str]] = {}
    for relationship in data_model.get("relationships") or []:
        if not isinstance(relationship, dict) or relationship.get("cardinality") != "one-to-many":
            continue
        parent, child = relationship.get("from_entity"), relationship.get("to_entity")
        if not parent or not child:
            continue
        children.setdefault(parent, []).append(child)
        parents.setdefault(child, []).append(parent)

    all_entities = {entity["name"] for entity in data_model.get("entities") or [] if isinstance(entity, dict) and entity.get("name")}
    visited: set[str] = set()
    components: list[set[str]] = []
    for name in sorted(all_entities):
        if name in visited:
            continue
        chain = {name}
        # Walk toward children: only cross an edge when the CURRENT node
        # has exactly one child (no fan-out to absorb here) AND the next
        # node has exactly one parent (not a merge point). The next node
        # is still ADDED even if it itself later fans out (e.g. Procedure
        # branches into Workpaper and AuditIssue) -- it's the chain's
        # legitimate last member; the walk just doesn't continue past it.
        cursor = name
        while len(children.get(cursor, [])) == 1:
            nxt = children[cursor][0]
            if len(parents.get(nxt, [])) != 1 or nxt in chain:
                break
            chain.add(nxt)
            cursor = nxt
        # Mirror walk toward parents.
        cursor = name
        while len(parents.get(cursor, [])) == 1:
            prev = parents[cursor][0]
            if len(children.get(prev, [])) != 1 or prev in chain:
                break
            chain.add(prev)
            cursor = prev
        visited |= chain
        if len(chain) >= 3:
            components.append(chain)
    return components


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
        data_model = None
        system_model = inputs.get("system-model")
        entity_names = [name for name in (system_model.get("entities") or []) if isinstance(name, str) and name] if isinstance(system_model, dict) else []
    if not entity_names:
        return []
    allowed = set(entity_names)
    # Deep hierarchy chains (3+ levels linked by one-to-many relationships,
    # e.g. a five-level fieldwork register) are exempt from the strict
    # per-entity dual-screen rule below -- one shared nested/inline editor
    # legitimately covers the whole chain. See _hierarchy_chain_components.
    chain_components = _hierarchy_chain_components(data_model)
    chain_members: set[str] = set().union(*chain_components) if chain_components else set()
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
    missing: list[str] = []
    for name in entity_names:
        if name in screens_by_entity:
            continue
        component = next((c for c in chain_components if name in c), None)
        if component and screens_by_entity.keys() & component:
            continue  # A sibling in the same hierarchy chain already has a screen -- covers the whole chain.
        missing.append(name)
    if missing:
        errors.append(f"mockup-spec.screens is missing CRUD coverage entirely for these entities: {missing} -- every entity must appear as entity_id on at least one screen (hierarchy-chain entities can share one screen with a sibling; standalone entities need their own)")
    single_screen_only = {name: ids for name, ids in screens_by_entity.items() if len(ids) < 2 and name not in chain_members}
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
