"""Shared runtime used by the CLI and HTTP API."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .agents import AgentLoader, DeterministicAgent, ProviderBackedAgent, create_handoff
from .documents import DocumentReader
from .erd import render_erd_mermaid
from .models import (
    Approval,
    ArtifactReference,
    ArtifactStatus,
    Comment,
    DataEntity,
    DataField,
    DataRelationship,
    ProjectState,
    RunReport,
    StepStatus,
    StoredArtifact,
    Task,
    WorkflowStatus,
    utc_now,
)
from .storage import (
    DEFAULT_PROJECT_ID,
    atomic_write,
    build_project_registry,
    build_project_store,
    rmtree_with_retries,
)
from .provider_config import load_mermaid_api_key, load_provider_settings, update_provider
from .providers import ProviderRequest, create_model_provider
from .tools.registry import resolve_tools
from .validators import data_model_relationships_reference_known_entities, entity_crud_coverage, no_control_characters_in_html, no_raw_ids_rendered_in_html, workflow_id_coverage


DEFAULT_AGENT_FILES = {
    "requirements.yaml": "id: requirements-agent\ndescription: Build the BRD and progressively richer business, solution, and system models.\ninputs: [project-inspection, brd]\noutputs: [brd, business-model, solution-model, system-model]\ntools: []\nconstraints:\n  - Preserve requirement intent.\n  - Produce traceable structured models.\n  - \"UPLOADED DOCUMENT (project-inspection.staged_document): this is the ONLY way you ever see the user's actual uploaded requirements document -- you have no filesystem or tool access, so nothing outside this JSON field exists for you to read. When it is present, its .text is the extracted document text: treat it as the authoritative source and write the brd output from it, preserving its structure, terminology, and any numbered requirements (e.g. BR-001) it already contains rather than inventing generic placeholder requirements. When it is null (no document uploaded yet), fall back to a small, reasonable default BRD instead of leaving brd empty.\"\n  - \"SUPPORTING REQUIREMENTS DOCUMENTS (system-references): If system-references is present in inputs, you MUST carefully read it and treat it as the current, authoritative understanding of the domain -- more specific and more recent than the original brd wherever the two disagree. This commonly happens when the BRD captured an early, general understanding and a supporting document captures a later, more detailed breakdown (e.g. from a follow-up meeting with a domain expert). When system-references describes entities, structure, or a hierarchy that differs from what business-model/solution-model/system-model currently contain, REPLACE the outdated entities/structure with what system-references describes -- do not keep old entities alongside the new ones just because they existed before. system-model.entities must reflect the CURRENT understanding, not a superset of every understanding ever provided.\"\n",
    "architecture.yaml": """id: architecture-agent
description: Model the structured entity-relationship data model, analyze an approved system model, enumerate the primary user workflows the system must support, and produce a diagram per workflow plus supporting architecture views.
inputs: [brd, business-model, solution-model, system-model, data-model, data-model-references, architecture-references]
outputs: [data-model, architecture-model, diagram-recommendations, diagrams]
tools: [artifact.read, artifact.write, mermaid.render]
constraints:
  - Do not modify approved business requirements.
  - Validate every diagram with the mermaid.render tool before finishing.
  - "DATA MODEL (data-model output only): produce ONLY structured entities/fields/relationships as data -- never write Mermaid or any diagram syntax for this output; the ERD diagram is generated deterministically from data-model by the pipeline itself, not by you. Every entity needs a real, meaningful field list (not just an id). Every relationship's from_entity and to_entity must reference a data-model.entities[].name that exists verbatim elsewhere in the same data-model -- do not invent, abbreviate, or misspell an entity name in a relationship. Prefer normalized, non-redundant entities that actually match the domain's structure (e.g. a five-level hierarchy should be modeled as five distinct related entities, not flattened into one)."
  - "SUPPORTING DATA MODEL DOCUMENTS (data-model-references): if present, treat it as the current, authoritative understanding of the domain's entities and structure -- more specific and more recent than the original brd/system-model wherever they disagree. REPLACE outdated entities with what data-model-references describes; do not keep old entities alongside new ones just because they existed before."
  - "Enumerate every primary user workflow the system must support (not just one). A workflow is an ordered chain of user-visible steps one actor performs to accomplish one goal. Put the list on architecture-model.workflows as objects with fields: id (snake_case slug), name, actor, purpose, entry_point_screen (a short slug for the screen the actor lands on to begin), and steps (an ordered list of short user-visible action phrases). Cover the whole system, not one narrow subflow."
  - Produce one sequenceDiagram per enumerated workflow, in architecture-model.workflows order, each named exactly "<workflow.name> Sequence".
  - Also produce one flowchart of the system components. Do NOT produce an erDiagram yourself -- the ERD comes from the already-approved data-model, rendered deterministically by the pipeline.
  - "Flowchart component diagrams must include every distinct component in solution-model.components -- not a simplified 3-5 box summary."
  - "Quote any Mermaid label containing special characters: A[\\"CAE / Auditor\\"] is fine, A[CAE / Auditor] is not. Never use <br/> -- use a real newline inside the quoted string when you need a line break."
""",
    "ux.yaml": """id: ux-agent
description: Produce a lightweight interactive mockup specification and real, self-contained HTML mockup pages from approved design artifacts.
inputs: [brd, system-model, data-model, architecture-model, diagrams, design-reference, mockup-references]
outputs: [mockup-spec, mockup-pages]
tools: [artifact.read, artifact.write]
constraints:
  - Use synthetic data.
  - Optimize for workflow validation rather than production UI quality.
  - Each mockup-pages entry is one complete, self-contained HTML document (inline <style>, no external assets) for one screen in mockup-spec.screens.
  - "SUPPORTING UX DOCUMENTS (mockup-references): If mockup-references is present in inputs, you MUST carefully inspect and strictly follow all screen flows, page sequences, layouts, navigation paths, and UI requirements described in those supporting documents. The supporting documents represent the user's explicit UX requirements and take precedence over default assumptions. mockup-references may hold several attached documents, each with its own `filename` -- if a comment or instruction names one of them specifically (e.g. \\"per field-mapping.xlsx\\" or \\"see the labels sheet\\"), treat that document as the authoritative source for this change over the others, even if it doesn't restate that document's content inline."
  - "Read architecture-model.workflows and organize screens by workflow: for each workflow, generate its entry_point_screen plus one screen per step in workflow.steps, in order. Every workflow screen must carry workflow_id EQUAL to one of architecture-model.workflows[].id verbatim -- copy the exact id string, do not invent shorter slugs or your own naming. Only an app-launcher-style landing screen uses the reserved workflow_id \\"__landing__\\"."
  - "WORKFLOW COVERAGE IS MANDATORY: every workflow in architecture-model.workflows must appear in mockup-spec.screens. If architecture-model.workflows has 8 entries, mockup-spec must have screens carrying all 8 of those workflow_id values, plus one __landing__ entry. Missing a workflow is a failure -- do not skip workflows to keep the list short."
  - "ENTITY CRUD COVERAGE IS MANDATORY: For every entity in data-model.entities (fall back to system-model.entities only if data-model is absent), generate dedicated CRUD screens: at minimum a list/register screen (browse, filter, search records) and a detail/edit/form screen (view, create, or edit record). Set entity_id to the exact entity name verbatim (e.g. \\"ia_auditable_entity\\"). For dedicated CRUD screens, set workflow_id to \\"\\" so they group cleanly under the Entity section in the review workspace navigation. (If a workflow step also acts as an entity editor, it may carry both workflow_id and entity_id). Missing an entity is a failure -- all entities must be represented."
  - "EXCEPTION -- deep hierarchy chains: if data-model.relationships describes a chain of 3+ entities linked by one-to-many relationships (e.g. a multi-level register like MajorProcess -> SubProcess -> RiskStatement -> TestObjective -> Procedure), do NOT generate 2 dedicated CRUD screens per level -- that produces an unmanageable pile of near-identical screens. Instead build ONE shared nested/inline editor screen for the whole chain (matching a UI where adding an item at any level never requires navigating to a separate screen), and set entity_id on that one screen to any single entity name from the chain -- it counts as coverage for every entity in that chain. Entities NOT part of such a chain (a single parent-child pair, or a standalone entity) still need their own dedicated list + form screens as usual."
  - "entity_id and workflow_id are INTERNAL METADATA ONLY, used purely to group screens in the review workspace's own navigation -- they are never user-facing product copy. NEVER render the raw entity_id or workflow_id string as visible text anywhere in a screen's HTML (not in <title>, headings, breadcrumbs, labels, or anywhere else). A screen titled for the ia_annual_plan entity reads as \\"Annual Plan Register\\", never \\"Annual Plan Register (ia_annual_plan)\\" or similar. Write natural, human-facing titles and labels as if entity_id/workflow_id did not exist."
  - Every screen shows synthetic-but-plausible data (real-looking names, dates, IDs, statuses) -- never lorem ipsum or single-row tables. Prefer richer screens (multiple sections, tables with several rows, forms with fields prefilled) over sparse placeholders.
  - "Cross-screen navigation: give any element that should move the user to another screen the attribute data-goto=\\"<screen id>\\", using the exact id values from mockup-spec.screens (a module tile that opens a module, a row that opens its detail view, a breadcrumb back-arrow, a form's cancel/save that returns to the list, tabs that switch between related screens). Do not write href=\\"something.html\\" or onclick=\\"location.href=...\\" -- these do nothing in the mockup preview; only data-goto works. Local page interactivity (tab panels within one screen, expand/collapse, hover states) with your own JS is fine and encouraged."
  - If design-reference is present, match its colors, typography, and component conventions, and place the new capability as an entry point within that reference system's own navigation (e.g. its menu or app launcher) rather than as a standalone unrelated page.
  - "TERMINOLOGY: for any primary navigation label, tab name, or section heading, use mockup-references' EXACT wording when it names one directly (e.g. if mockup-references says the tabs are \\"Audit Plans\\" and \\"Audit Assignments\\", use those exact two labels -- do not substitute a synonym like \\"Engagements\\" even though it's valid audit terminology in prose elsewhere). Generic domain vocabulary is fine for body copy and descriptions; primary navigation must match the reference doc's own vocabulary exactly."
  - "NESTED/ATTACHED RECORDS: an entity that mockup-references (or data-model's own relationships) describes as an annotation, comment, or note attached to a specific parent record (e.g. review notes/comments raised against a specific workpaper or procedure) belongs INLINE within that parent's own screen -- shown alongside the record it's attached to, with its own add/reply affordance right there -- not as a separate, independent top-level CRUD screen the user has to navigate away to reach. Use judgement based on how the reference document actually describes the interaction, not just the data model's cardinality alone."
  - "The chain-hierarchy exception above still requires the ONE shared editor screen to be ACTUALLY EDITABLE, not a read-only summary: real Add buttons at every level, inline editable fields, and visible affordances for editing/removing an existing item at any level -- matching \\"adding an item at any level never requires navigating to a separate screen\\", which only works if the screen truly lets you add/edit there."
  - "MASTER-DETAIL HIERARCHIES: if an entity's own screen would need to show, for ONE record of that entity, a full table/list of a DIFFERENT related entity's records (e.g. one annual plan's list of audit projects, one project's list of workpapers), that is TWO screens, not one -- a top-level LIST screen that browses/selects among ALL records of the first entity (e.g. every plan year), and a separate DETAIL screen for exactly one of those records showing its related child records. Do not collapse both into a single screen that jumps straight to one hardcoded record's detail -- that leaves no way to browse or select among the other records, which the entity's own \\"list of X\\" key_elements bullet already promises. Read key_elements literally: if it mixes \\"list of <entity>\\" phrasing with a table/detail of a DIFFERENT entity in the same screen entry, that is exactly this mistake -- split it into the two screens described above, both carrying that entity's entity_id."
  - "STRUCTURAL PARITY (\\"X has the same structure/capabilities as Y\\"): when a reference document states that one entity carries the same structure, feature set, or capabilities as another already-modeled entity (e.g. \\"an audit assignment carries the same structure and capabilities as an audit project: the three folders, the fieldwork register, issue tracking and the wrap-up documents\\"), this means X gets its OWN parallel set of screens that mirror Y's structure -- own identity fields, own header/breadcrumb showing X's own record (not Y's), own back-navigation that returns to X's own register, not Y's. It does NOT mean routing X's rows to literally open Y's existing screens. Reusing Y's screens for X is a failure mode: it leaves X's detail view showing Y's data and navigating back into Y's list, which is wrong regardless of how similar the two entities' structures are."
  - "VERBATIM ENUMERATIONS: when a reference document names an exact, finite list of values for a status, category, rating, or option field (e.g. an issue status of Open / Overdue / Closed (verified) / Closed (unverified) / Closed (pending), or a High/Medium/Low rating), reproduce that list exactly -- same count, same wording, same order -- on every screen that field appears, rather than inventing a shorter or reworded set of your own."
  - "FRESH/EMPTY STATE: for any entity whose reference material describes it as created through a setup or creation flow (e.g. creating a new audit project instantiates its folder structure before any content exists), include at least one screen depicting that entity immediately after creation -- the structural chrome (folders, tabs, sections) all present and reachable, but each section showing its genuine empty state (e.g. \\"No planning documents uploaded yet\\" plus the affordance to add one) rather than only ever showing a fully populated example."
  - "ICON SAFETY: use only ordinary, single-codepoint Unicode emoji or symbol characters for icons (the same kind used elsewhere in this spec, e.g. a plain folder or document glyph) -- never emit multi-codepoint ZWJ emoji sequences, variation selectors, or any other multi-byte combining sequence, and never emit raw control characters (byte values below 0x20 other than tab/newline) anywhere in generated HTML. These have previously corrupted into stray control bytes during generation and rendered as broken icons."
  - "CONFIGURATION/VERSIONING SEMANTICS: read requirement language carefully for phrasing that implies configuration or versioning, not a single flat record -- e.g. \\"a customisable feedback form... it should either be a template or versioned doc, so we can create multiple templates and select which is active\\" describes MULTIPLE saved templates plus an explicit active/inactive selection, not a single uploaded file. Model such requirements as a list of versions/templates with a visible active-selection control, matching the described semantics rather than flattening them into a generic single-file upload."
""",
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
    inputs: [brd, system-references]
    outputs: [business-model, solution-model, system-model]
    depends_on: [requirements]
  - id: requirements-approval
    name: Approve requirements model
    type: human-approval
    inputs: [system-model]
    depends_on: [requirements-model]
  - id: data-modeling
    name: Model core entities and relationships
    type: agent
    agent: architecture-agent
    inputs: [brd, business-model, solution-model, system-model, data-model-references]
    outputs: [data-model]
    depends_on: [requirements-approval]
  - id: data-model-approval
    name: Approve data model
    type: human-approval
    inputs: [data-model]
    depends_on: [data-modeling]
  - id: architecture
    name: Generate architecture recommendations
    type: agent
    agent: architecture-agent
    inputs: [brd, business-model, solution-model, system-model, data-model, architecture-references]
    outputs: [architecture-model, diagram-recommendations, diagrams]
    depends_on: [data-model-approval]
  - id: architecture-approval
    name: Approve architecture
    type: human-approval
    inputs: [architecture-model]
    depends_on: [architecture]
  - id: mockups
    name: Generate mockup specification
    type: agent
    agent: ux-agent
    inputs: [brd, system-model, data-model, architecture-model, diagrams, design-reference, mockup-references]
    outputs: [mockup-spec, mockup-pages]
    depends_on: [architecture-approval]
"""


class DesignRuntime:
    """Orchestrate workflows while keeping persistence in :class:`ProjectStore`.

    One runtime is bound to one project. To serve multiple projects from
    one process, use :class:`RuntimeRegistry` below.
    """

    def __init__(self, root: Path | str, project_id: str = DEFAULT_PROJECT_ID):
        self.store = build_project_store(root, project_id=project_id)
        # See _require_initialized(): _sync_config() only needs to run once
        # per process, not once per request.
        self._config_synced = False

    @property
    def root(self) -> Path:
        return self.store.paths.root

    @property
    def _database_url(self) -> str | None:
        return getattr(self.store, "database_url", None)

    def initialize(self, project_id: str | None = None) -> ProjectState:
        state = self.store.initialize(project_id)
        self._sync_config()
        self._config_synced = True
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

    def _sync_config(self) -> None:
        """Reconcile the on-disk agent/workflow YAML with Postgres, when in
        Postgres mode. A file that's actually PRESENT on disk is always
        treated as current truth and (if it differs) pushed into Postgres --
        never overwritten from the DB, even if the DB holds different
        content, because that content could be a local edit not yet synced
        (an earlier version of this method got that backwards: it always
        restored from the DB first, which silently clobbered a customization
        made after the DB row was first seeded, before it ever reached
        Postgres -- caught by test_project_config_durability.py, not by
        reading the code). Only a file that's genuinely MISSING (the
        ephemeral-disk-wipe case this exists for) gets restored from the DB.

        1. Restore only what's missing from disk, using the DB's last-known
           content, if a `project_config` row exists.
        2. `_write_defaults()` -- the existing filesystem-only fallback,
           unchanged. A no-op for anything step 1 just restored; it only
           fires for a project whose DB row doesn't exist yet, or in plain
           filesystem mode where there's no DB at all.
        3. Push whatever's on disk now back into Postgres -- restored,
           defaulted, or a real customization that was sitting on disk
           unsynced -- but only when it actually differs from the DB, so
           this doesn't upsert on every single request once nothing's
           changed.
        """
        database_url = getattr(self.store, "database_url", None)
        config = self.store.load_config() if database_url else None
        if config is not None:
            for name, content in config["agent_files"].items():
                path = self.store.paths.agents / name
                if not path.exists():
                    atomic_write(path, content)
            workflow_path = self.store.paths.workflows / "design-pipeline.yaml"
            if not workflow_path.exists():
                atomic_write(workflow_path, config["workflow_file"])
            if config.get("staged_brd_content"):
                brd_path = self.store.paths.input / (config.get("staged_brd_filename") or "BRD.md")
                if not brd_path.exists():
                    atomic_write(brd_path, config["staged_brd_content"])
        self._write_defaults()
        if database_url:
            agent_files = {path.name: path.read_text(encoding="utf-8") for path in self.store.paths.agents.glob("*.yaml")}
            workflow_file = (self.store.paths.workflows / "design-pipeline.yaml").read_text(encoding="utf-8")
            if config is None or config["agent_files"] != agent_files or config["workflow_file"] != workflow_file:
                self.store.save_config(agent_files, workflow_file)

    def _require_initialized(self) -> None:
        if not self.store.is_initialized():
            raise FileNotFoundError("project is not initialized; run `design init`")
        # Self-heal the on-disk agent/workflow YAML (and, via _sync_config,
        # the staged BRD). In Postgres mode, `is_initialized()` reflects a
        # `project_state` row in the database -- durable -- but the
        # agent/workflow config files themselves used to live only on local
        # disk (see project_config's docstring in db/schema.py). On a host
        # with an ephemeral filesystem (e.g. Render), those files are gone
        # after any redeploy or restart while the Postgres row still says
        # "initialized", so every call downstream of here (workflow(),
        # restart_generation(), run(), ...) would otherwise crash with
        # FileNotFoundError reading design-pipeline.yaml -- observed live in
        # production.
        #
        # Only actually needs to run once per process, not once per
        # request: _sync_config() does a Postgres round-trip plus reading
        # every agent/workflow file, and this method sits at the top of ~20
        # other methods -- one page load's worth of API calls was paying
        # that cost that many times over, observed live as "every page load
        # is slow" (in both local dev and production alike -- nothing
        # Render/Neon-specific about it, this ran the same regardless of
        # host). Nothing else touches this process's own disk between
        # requests, so a second reconciliation in the same process can only
        # ever reach the same conclusion as the first.
        for directory in (self.store.paths.agents, self.store.paths.workflows, self.store.paths.input):
            directory.mkdir(parents=True, exist_ok=True)
        if not self._config_synced:
            self._sync_config()
            self._config_synced = True

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
            "provider": load_provider_settings(self.root, database_url=self._database_url).public_status(),
        }

    def set_provider(self, provider: str) -> dict[str, object]:
        """Switch the active model provider by rewriting `.env` in place.

        Only the `DESIGN_PIPELINE_PROVIDER` line changes -- API keys are
        never touched here, so switching to a provider with no key
        configured yet is allowed and just shows up as `configured: false`
        (matches `public_status()`), same as always.
        """
        update_provider(self.root, provider, database_url=self._database_url)
        return load_provider_settings(self.root, database_url=self._database_url).public_status()

    def _design_reference_parent_version(self) -> int | None:
        try:
            return self.store.artifacts.get("design-reference").metadata.version
        except FileNotFoundError:
            return None

    def _save_design_reference(self, data: dict[str, Any], generated_by: dict[str, str]) -> StoredArtifact:
        artifact = self.store.artifacts.save(
            "design-reference", "design-reference", data,
            generated_by=generated_by,
            parent_version=self._design_reference_parent_version(),
        )
        self.store.append_event("DESIGN_REFERENCE_SET", artifact_id="design-reference", details={"version": artifact.metadata.version, "source": generated_by["provider"]})
        return artifact

    def set_design_reference(self, data: dict[str, Any]) -> StoredArtifact:
        """Store a structured design reference (colors, typography, layout/
        component conventions) that the mockups step uses to style its
        output -- one of three ways to acquire one (see also
        `generate_design_reference` and `ingest_design_reference_*`).

        Ingested structurally, like the BRD, rather than agent-generated --
        the source of truth is whatever produced `data` (e.g. a live
        inspection of a reference system), not a model's own writing.
        Calling any of the three replaces the same artifact with a new
        version; nothing else needs a matching call to keep working, since
        `mockups` treats this input as optional.
        """
        self._require_initialized()
        return self._save_design_reference(data, {"agent": "runtime", "provider": "manual-capture", "model": "browser-inspection"})

    def ingest_design_reference_text(self, content: str, filename: str = "design-reference.md") -> StoredArtifact:
        """Turn an uploaded reference document (a style guide, brand notes,
        etc.) into a design reference. Reuses the same text-extraction
        `DocumentReader` already uses for the BRD -- the file itself is
        never retained, only its extracted text."""
        self._require_initialized()
        document = DocumentReader(self.root).read_bytes(content.encode("utf-8"), filename)
        return self._save_design_reference({"source": f"file:{document.filename}", "notes": document.content}, {"agent": "runtime", "provider": "file-ingestion", "model": document.media_type})

    def ingest_design_reference_bytes(self, content: bytes, filename: str) -> StoredArtifact:
        self._require_initialized()
        document = DocumentReader(self.root).read_bytes(content, filename)
        return self._save_design_reference({"source": f"file:{document.filename}", "notes": document.content}, {"agent": "runtime", "provider": "file-ingestion", "model": document.media_type})

    def generate_design_reference(self, name: str, notes: str | None = None) -> StoredArtifact:
        """Ask the configured live provider to describe a named reference
        app or system's well-known UI conventions from its own knowledge --
        for a reference with no local instance to inspect (e.g. "WhatsApp").
        Needs a live provider; there's nothing for the deterministic stub
        to research.
        """
        self._require_initialized()
        settings = load_provider_settings(self.root, database_url=self._database_url)
        if settings.provider == "stub":
            raise ValueError("researching a design reference needs a live provider; set DESIGN_PIPELINE_PROVIDER to openai, anthropic, or gemini in .env")
        provider = create_model_provider(settings)
        system_prompt = (
            "You are a UI design research assistant. From your own knowledge, describe the well-known visual "
            "design conventions of the named application or system. Return only one valid JSON object with keys: "
            "colors (an object mapping a role name to a hex color), typography, layout_notes (a list of strings), "
            "navigation (how users move around it, e.g. tab bar, sidebar, app launcher), and components (a list of "
            "notable, reusable UI patterns). Do not use Markdown fences."
        )
        response = provider.generate(ProviderRequest(system_prompt=system_prompt, user_prompt=json.dumps({"name": name, "notes": notes}), temperature=0.0))
        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{provider.name} returned invalid JSON for the design reference: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{provider.name} returned a non-object JSON result for the design reference")
        data.setdefault("source", f"researched:{name}")
        data.setdefault("name", name)
        return self._save_design_reference(data, {"agent": "design-reference-research", "provider": provider.name, "model": provider.model})

    def ingest_brd(self, source: Path | str):
        self._require_initialized()
        document = DocumentReader(self.store.paths.input).ingest_brd(Path(source))
        self._persist_staged_brd(document)
        self.store.append_event("BRD_INGESTED", details={"filename": document.filename, "path": document.path})
        return document

    def ingest_brd_text(self, content: str, filename: str = "BRD.md"):
        self._require_initialized()
        document = DocumentReader(self.store.paths.input).ingest_text(content, filename)
        self._persist_staged_brd(document)
        self.store.append_event("BRD_INGESTED", details={"filename": document.filename, "path": document.path})
        return document

    def ingest_brd_bytes(self, content: bytes, filename: str):
        self._require_initialized()
        document = DocumentReader(self.store.paths.input).ingest_bytes(content, filename)
        self._persist_staged_brd(document)
        self.store.append_event("BRD_INGESTED", details={"filename": document.filename, "path": document.path})
        return document

    def _persist_staged_brd(self, document) -> None:
        # DocumentReader already wrote the extracted text to local disk
        # (`.design/<project>/input/BRD.md`) -- mirror it into Postgres too,
        # in DB mode, so an upload survives a redeploy/restart that happens
        # before the user clicks Generate (the file is read fresh on every
        # requirements-step run, not just once, so this matters beyond the
        # very first generation too).
        if hasattr(self.store, "save_staged_brd"):
            self.store.save_staged_brd(document.filename, document.content)

    # ---- Multi-document supplementary references per stage --------------
    # A `{stage}-references` artifact holds a list of extracted reference
    # documents (text-only, via DocumentReader -- same .docx/.md/.txt/.rst
    # types the BRD already accepts). Any workflow step listing
    # `<stage>-references` in its inputs sees them alongside its normal
    # upstream artifacts; `_load_inputs` already silently skips missing
    # inputs, so unattached stages stay unaffected. Stages not restricted
    # to a fixed list -- the caller passes any stage name and gets a
    # namespaced artifact back.

    _REFERENCE_ARTIFACT_TYPE = "references"

    @staticmethod
    def _reference_artifact_id(stage: str) -> str:
        s = stage.strip().strip('-')
        if s.endswith("s") and s not in {"diagrams"}:
            s = s[:-1]
        return f"{s}-references"

    def _load_references_content(self, stage: str) -> list[dict[str, str]]:
        primary = self._reference_artifact_id(stage)
        raw_stage = f"{stage.strip().strip('-')}-references"
        for candidate in [primary, raw_stage]:
            try:
                return list(self.store.artifacts.get(candidate).content or [])
            except FileNotFoundError:
                continue
        return []

    def _save_references(self, stage: str, entries: list[dict[str, str]], event: str, filename: str) -> StoredArtifact:
        artifact_id = self._reference_artifact_id(stage)
        try:
            parent_version = self.store.artifacts.get(artifact_id).metadata.version
        except FileNotFoundError:
            parent_version = None
        artifact = self.store.artifacts.save(
            artifact_id, self._REFERENCE_ARTIFACT_TYPE, entries,
            generated_by={"agent": "runtime", "provider": "file-ingestion", "model": stage},
            parent_version=parent_version,
        )
        self.store.append_event(event, artifact_id=artifact_id, details={"stage": stage, "filename": filename, "count": len(entries)})
        return artifact

    def add_reference_text(self, stage: str, content: str, filename: str) -> StoredArtifact:
        """Attach a supplementary reference document to a stage from text.
        Same-filename attaches replace in place; different filenames append.
        """
        self._require_initialized()
        document = DocumentReader(self.root).read_bytes(content.encode("utf-8"), filename)
        entries = [entry for entry in self._load_references_content(stage) if entry.get("filename") != document.filename]
        entries.append({"filename": document.filename, "media_type": document.media_type, "content": document.content})
        return self._save_references(stage, entries, "REFERENCE_ADDED", document.filename)

    def add_reference_bytes(self, stage: str, content: bytes, filename: str) -> StoredArtifact:
        self._require_initialized()
        document = DocumentReader(self.root).read_bytes(content, filename)
        entries = [entry for entry in self._load_references_content(stage) if entry.get("filename") != document.filename]
        entries.append({"filename": document.filename, "media_type": document.media_type, "content": document.content})
        return self._save_references(stage, entries, "REFERENCE_ADDED", document.filename)

    def list_references(self, stage: str) -> list[dict[str, str]]:
        self._require_initialized()
        return self._load_references_content(stage)

    def remove_reference(self, stage: str, filename: str) -> StoredArtifact | None:
        """Drop one attachment by filename. Returns the new artifact
        version, or None if nothing matched (harmless idempotent no-op)."""
        self._require_initialized()
        entries = self._load_references_content(stage)
        remaining = [entry for entry in entries if entry.get("filename") != filename]
        if len(remaining) == len(entries):
            return None
        return self._save_references(stage, remaining, "REFERENCE_REMOVED", filename)

    def edit_reference(self, stage: str, filename: str, content: str) -> StoredArtifact:
        """Overwrite one attachment's text in place -- for a small wording
        tweak, this beats delete-then-reupload: the content is already
        plain extracted text (see add_reference_text/_bytes), so there's
        no file to re-parse, just a direct replace."""
        self._require_initialized()
        entries = self._load_references_content(stage)
        matched = False
        updated: list[dict[str, str]] = []
        for entry in entries:
            if entry.get("filename") == filename:
                updated.append({**entry, "content": content})
                matched = True
            else:
                updated.append(entry)
        if not matched:
            raise FileNotFoundError(f"no reference attachment named '{filename}' on stage '{stage}'")
        return self._save_references(stage, updated, "REFERENCE_EDITED", filename)

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

    def _project_inspection_content(self, project_id: str) -> dict[str, Any]:
        # The "requirements" step's ONLY input is this artifact -- a live
        # provider has no filesystem access and no working tool to fetch a
        # file itself (the agent YAML's declared `project.read` tool was
        # never actually implemented; see tools/registry.py), so if the
        # extracted BRD text isn't embedded directly here, the model
        # receives nothing but these bare paths and (reasonably) returns
        # empty output for brd/business-model/solution-model/system-model.
        # This was a real production incident: every one of those came back
        # as `{}` for a freshly uploaded document.
        document = DocumentReader(self.store.paths.input).read_brd()
        return {
            "project_id": project_id,
            "root": str(self.root),
            "design_directory": str(self.store.paths.design),
            "staged_document": {"filename": document.filename, "text": document.content} if document else None,
        }

    def _load_inputs(self, names: list[str]) -> tuple[dict[str, Any], list[ArtifactReference], list[str]]:
        values: dict[str, Any] = {}
        references: list[ArtifactReference] = []
        requirements: set[str] = set()
        for name in names:
            try:
                artifact = self.store.artifacts.get(name)
            except FileNotFoundError:
                if name.endswith("-references"):
                    base = name[:-len("-references")]
                    alt = f"{base}s-references" if not base.endswith("s") else f"{base[:-1]}-references"
                    try:
                        artifact = self.store.artifacts.get(alt)
                    except FileNotFoundError:
                        continue
                else:
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
        settings = load_provider_settings(self.root, database_url=self._database_url)
        if settings.provider == "stub":
            values = DeterministicAgent(definition, self.store.paths.input).run(outputs, inputs, comments, instruction)
            return values, {"agent": agent_id, "provider": "stub", "model": "deterministic-fixture"}
        provider = create_model_provider(settings)
        tools = resolve_tools(definition.tools, mermaid_api_key=load_mermaid_api_key(self.root))
        # Only the ux-agent produces mockup-spec, and that's the only output
        # whose workflow_ids need cross-referencing against
        # architecture-model. Adding more validators is just extending this
        # list -- keep them agent-specific to avoid running irrelevant checks.
        if agent_id == "ux-agent":
            validators = [workflow_id_coverage, entity_crud_coverage, no_raw_ids_rendered_in_html, no_control_characters_in_html]
        elif agent_id == "architecture-agent":
            # Only inspects values.get("data-model"), so it's a safe no-op
            # on the "architecture" step's own call (outputs=[architecture-model,
            # diagram-recommendations, diagrams], no "data-model" key) --
            # architecture-agent now serves two different steps.
            validators = [data_model_relationships_reference_known_entities]
        else:
            validators = []
        agent = ProviderBackedAgent(definition, provider, tools=tools, max_tool_iterations=settings.max_tool_iterations, output_validators=validators)
        values = agent.run(outputs, inputs, comments, instruction)
        if "diagrams" in values:
            # Trust the mermaid.render tool's own validated result over the
            # model's freehand restatement of it in its final JSON answer --
            # field names, and even the exact Mermaid source, can drift when
            # a model retypes a result instead of reusing it verbatim.
            rendered = {call["arguments"].get("name", index): call["result"] for index, call in enumerate(agent.last_tool_calls) if call["tool"] == "mermaid.render"}
            if rendered:
                values["diagrams"] = list(rendered.values())
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
                    values = {"project-inspection": self._project_inspection_content(state.project_id)}
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
        settings = load_provider_settings(self.root, database_url=self._database_url)
        if settings.provider == "stub":
            raise ValueError("live generation is not selected; set DESIGN_PIPELINE_PROVIDER to openai, anthropic, or gemini in .env, then restart the server")
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
                values = {"project-inspection": self._project_inspection_content(state.project_id)}
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
        self.store.artifacts.attach_comment(artifact_id, comment.id, artifact.metadata.version)
        self.store.append_event("COMMENT_ADDED", artifact_id=artifact_id, details={"comment_id": comment.id})
        return comment

    def retry(self, artifact_id: str, instruction: str | None = None):
        self._require_initialized()
        current = self.store.artifacts.get(artifact_id)
        agent_id = current.metadata.generated_by.agent
        if agent_id == "runtime":
            raise ValueError("deterministic inspection artifacts do not support agent retry")

        # If this artifact is co-generated with siblings (one workflow step
        # producing multiple outputs -- e.g. `mockup-spec` + `mockup-pages`,
        # or `architecture-model` + `diagram-recommendations` + `diagrams`),
        # regenerate the whole sibling set in one call so they stay
        # consistent. Before this, retrying one silently drifted the others
        # out of sync -- observed live as `mockup-pages` at 10 screens while
        # `mockup-spec` was still at 5, leaving half the pages unreachable.
        step = None
        step_outputs = [artifact_id]
        for candidate in self.workflow().steps:
            if artifact_id in candidate.outputs:
                step = candidate
                if len(candidate.outputs) > 1:
                    step_outputs = list(candidate.outputs)
                break

        # Load comments from every sibling in this step, not just the
        # named target. Users comment on the mockup-pages screen they see,
        # then retry mockup-spec (or vice versa) -- both sets of feedback
        # must reach the agent since one call regenerates both. Only OPEN
        # comments -- once a comment has actually been applied by a
        # successful retry (below), it's marked resolved so it doesn't
        # keep getting resent (and re-confusing the model) on every future
        # retry indefinitely.
        comments: list[Comment] = []
        seen_comment_ids: set[str] = set()
        for output_id in step_outputs:
            for comment in self.store.list_comments(output_id):
                if comment.status == "open" and comment.id not in seen_comment_ids:
                    seen_comment_ids.add(comment.id)
                    comments.append(comment)

        # Resolve inputs against the *latest* versions of each declared
        # upstream artifact, not this artifact's old pinned refs. Before
        # this, retrying used stale snapshots -- e.g. `mockup-pages` v6
        # regenerating against `architecture-model v4` even though the
        # current architecture was v5 with an entirely different workflow
        # set, so cross-artifact validators saw the wrong data.
        input_ids = list(step.inputs) if step is not None else [reference.logical_id for reference in current.metadata.inputs]
        inputs: dict[str, Any] = {artifact_id: current.content}
        input_refs: list[ArtifactReference] = []
        input_requirements: set[str] = set()
        for input_id in input_ids:
            try:
                upstream = self.store.artifacts.get(input_id)
            except FileNotFoundError:
                if input_id.endswith("-references"):
                    base = input_id[:-len("-references")]
                    alt = f"{base}s-references" if not base.endswith("s") else f"{base[:-1]}-references"
                    try:
                        upstream = self.store.artifacts.get(alt)
                    except FileNotFoundError:
                        continue
                else:
                    continue
            inputs[input_id] = upstream.content
            input_refs.append(ArtifactReference(logical_id=input_id, version=upstream.metadata.version))
            input_requirements.update(upstream.metadata.requirements)

        values, generated_by = self._execute_agent(agent_id, step_outputs, inputs, comments, instruction)

        primary: Any = None
        regenerated: list[str] = []
        for output_id in step_outputs:
            if output_id not in values:
                continue
            try:
                previous = self.store.artifacts.get(output_id)
                parent_version = previous.metadata.version
                previous_type = previous.metadata.type
            except FileNotFoundError:
                parent_version = None
                previous_type = output_id
            # Record the freshly-resolved input refs, not the stale ones
            # from the prior version -- so future retries see accurate
            # provenance and future validators still work.
            saved_requirements = sorted(input_requirements) if input_requirements else current.metadata.requirements
            saved = self.store.artifacts.save(output_id, previous_type, values[output_id], generated_by=generated_by, inputs=input_refs, requirements=saved_requirements, parent_version=parent_version)
            if parent_version is not None:
                self.store.artifacts.update_status(output_id, ArtifactStatus.SUPERSEDED, parent_version)
            regenerated.append(output_id)
            if output_id == artifact_id:
                primary = saved

        state = self.store.load_state()
        for step in self.workflow().steps:
            if any(output in step.inputs for output in regenerated) and step.type == "human-approval":
                state.step_states[step.id] = StepStatus.PENDING
                state.workflow_status = WorkflowStatus.PAUSED
        self.store.save_state(state)
        self._resolve_comments(comments)
        self.store.append_event("ARTIFACT_RETRIED", artifact_id=artifact_id, details={"version": primary.metadata.version, "parent_version": current.metadata.version, "instruction": instruction, "co_regenerated": regenerated})
        return primary

    def _resolve_comments(self, comments: list[Comment]) -> None:
        """Mark comments as applied once a retry that used them succeeds,
        so they stop being resent (and re-confusing the model) on every
        subsequent retry. Comments stay in history -- resolved, not
        deleted -- same as every other artifact in this app never being
        silently thrown away."""
        for comment in comments:
            comment.status = "resolved"
            comment.resolved_at = utc_now()
            self.store.save_comment(comment)

    def retry_screen(self, screen_id: str, instruction: str | None = None) -> StoredArtifact:
        """Regenerate exactly ONE mockup screen's HTML, splicing it back
        into the existing `mockup-pages` array in code -- every other
        screen is guaranteed untouched, unlike `retry("mockup-pages")`
        which rewrites the whole set in one LLM call and just trusts the
        model to leave the rest alone. Use this for a comment scoped to
        one screen/element; use the full `retry` for a change that should
        apply everywhere (better yet, put that in the mockup-references
        supporting doc so it's a standing rule, not a repeated comment).
        """
        self._require_initialized()
        pages_artifact = self.store.artifacts.get("mockup-pages")
        pages = [dict(page) for page in (pages_artifact.content or [])]
        index = next((i for i, page in enumerate(pages) if page.get("screen_id") == screen_id), None)
        if index is None:
            raise ValueError(f"no mockup page found for screen_id '{screen_id}'")

        agent_id = pages_artifact.metadata.generated_by.agent
        if agent_id == "runtime":
            raise ValueError("deterministic inspection artifacts do not support agent retry")

        spec_artifact = self.store.artifacts.get("mockup-spec")

        # Only OPEN comments scoped to this specific screen (element- or
        # screen-level) -- a comment on a different screen has no business
        # steering this one, and an already-applied (resolved) comment
        # shouldn't keep getting resent on every future retry.
        comments = [comment for comment in self.store.list_comments("mockup-pages") if comment.status == "open" and (comment.location or {}).get("screen_id") == screen_id]

        step = next((candidate for candidate in self.workflow().steps if "mockup-pages" in candidate.outputs), None)
        input_ids = [input_id for input_id in (step.inputs if step else []) if input_id not in ("mockup-spec",)]
        # Full context (per design decision): the whole current page set and
        # spec are included so the regenerated screen stays visually and
        # structurally consistent with the rest, even though only one
        # screen's HTML is requested back.
        inputs: dict[str, Any] = {"mockup-pages": pages, "mockup-spec": spec_artifact.content, "target_screen_id": screen_id}
        input_refs: list[ArtifactReference] = [ArtifactReference(logical_id="mockup-spec", version=spec_artifact.metadata.version)]
        for input_id in input_ids:
            try:
                upstream = self.store.artifacts.get(input_id)
            except FileNotFoundError:
                continue
            inputs[input_id] = upstream.content
            input_refs.append(ArtifactReference(logical_id=input_id, version=upstream.metadata.version))

        scoped_instruction = (
            f"Regenerate ONLY the screen whose screen_id is exactly '{screen_id}' (see target_screen_id). "
            "The full mockup-pages array and mockup-spec are included as reference for this screen's style, "
            "navigation, and structure -- do not return them, and do not describe changes to any other screen. "
            "That reference also makes the other screens' CURRENT content the source of truth for shared "
            "terminology: where this screen shows a label, field name, button, or heading for the same concept "
            "(entity, action, status, etc.) that another screen already uses different wording for, match the "
            "other screen's current wording exactly, even if that isn't spelled out explicitly below -- "
            "cross-screen consistency is expected by default, not just when asked for. "
            "Return your answer as mockup-page-patch: a single object {screen_id, html} for just this one screen."
        ) + (f"\nAlso apply this specific instruction: {instruction}" if instruction else "")

        values, generated_by = self._execute_agent(agent_id, ["mockup-page-patch"], inputs, comments, scoped_instruction)
        patch = self._coerce_mockup_page_patch(values.get("mockup-page-patch"))
        if not isinstance(patch, dict) or not patch.get("html"):
            # Include what actually came back -- otherwise this error is a
            # dead end, same problem the "did not produce declared
            # output(s)" error had before it got the same treatment.
            raw = json.dumps(values.get("mockup-page-patch"), default=str)
            if len(raw) > 500:
                raw = raw[:500] + "...(truncated)"
            raise ValueError(f"{generated_by.get('provider', 'the provider')} did not return a valid mockup-page-patch for '{screen_id}' (raw: {raw})")
        patch["screen_id"] = screen_id  # guard against the model relabeling it
        pages[index] = patch

        saved = self.store.artifacts.save(
            "mockup-pages", pages_artifact.metadata.type, pages,
            generated_by=generated_by, inputs=input_refs, requirements=pages_artifact.metadata.requirements,
            parent_version=pages_artifact.metadata.version,
        )
        self.store.artifacts.update_status("mockup-pages", ArtifactStatus.SUPERSEDED, pages_artifact.metadata.version)
        self._resolve_comments(comments)
        self.store.append_event("MOCKUP_SCREEN_RETRIED", artifact_id="mockup-pages", details={"screen_id": screen_id, "version": saved.metadata.version, "parent_version": pages_artifact.metadata.version, "instruction": instruction})
        return saved

    def add_mockup_screen(self, description: str, link_from_screen_id: str | None = None) -> StoredArtifact:
        """Append exactly ONE new screen to the mockup set -- both
        `mockup-spec` (its entry) and `mockup-pages` (its HTML) -- without
        touching or resending any other screen, and without re-running the
        entity/workflow CRUD-coverage validators that a full `retry` would.

        Exists for the gap `retry_screen` structurally can't fill: a
        comment sometimes reveals a screen is *missing* entirely (e.g. "the
        Create button should open a real Create screen, not a modal" when
        no such screen exists yet) -- `retry_screen` can only rewrite one
        already-existing screen's HTML, so the model's only way to satisfy
        that request within those bounds is to fake it in-place (a modal).
        A full `retry("mockup-pages")` *can* add a screen, but it re-sends
        every existing screen back through the model in one call and just
        trusts it not to alter any of them -- observed live to drift.  This
        splits the difference: the model only ever sees (and is only ever
        allowed to touch) the one screen it's linking from, everything else
        is passed through unchanged in code exactly like `retry_screen`.
        """
        if not description or not description.strip():
            raise ValueError("a description of the new screen is required")
        return self._create_linked_mockup_screen(description.strip(), link_from_screen_id, mode="add")

    def split_mockup_screen(self, screen_id: str, extract_description: str) -> StoredArtifact:
        """Move part of one existing screen out into a brand-new linked
        screen -- both `mockup-spec` and `mockup-pages` -- leaving every
        other screen untouched. `screen_id` is rewritten to remove the
        extracted content (replaced with a link to the new screen); the
        extracted content becomes the new screen's whole page.

        Exists for the mirror-image gap `add_mockup_screen` doesn't cover:
        sometimes a screen isn't *missing* something, it's *carrying* two
        things that should be two screens -- observed live: a plan-year
        list screen whose spec literally called for both "list of annual
        plans" and "projects table" as separate bullets, but the model
        merged them into one screen, so opening "Annual Audit Plans" always
        jumped straight to one hardcoded year's projects instead of
        letting you pick a year first. A full mockup regeneration can fix
        this but re-sends and risks drifting every other screen; this
        keeps the blast radius to exactly the two screens involved.
        """
        if not screen_id or not screen_id.strip():
            raise ValueError("screen_id is required")
        if not extract_description or not extract_description.strip():
            raise ValueError("a description of what to extract is required")
        return self._create_linked_mockup_screen(extract_description.strip(), screen_id.strip(), mode="split")

    def _create_linked_mockup_screen(self, description: str, link_from_screen_id: str | None, *, mode: str) -> StoredArtifact:
        self._require_initialized()

        pages_artifact = self.store.artifacts.get("mockup-pages")
        spec_artifact = self.store.artifacts.get("mockup-spec")
        pages = [dict(page) for page in (pages_artifact.content or [])]
        screens = [dict(screen) for screen in (spec_artifact.content or {}).get("screens", [])]
        existing_ids = {screen.get("id") for screen in screens}

        source_index = None
        if link_from_screen_id is not None:
            source_index = next((i for i, page in enumerate(pages) if page.get("screen_id") == link_from_screen_id), None)
            if source_index is None:
                raise ValueError(f"no mockup page found for screen_id '{link_from_screen_id}'")

        agent_id = pages_artifact.metadata.generated_by.agent
        if agent_id == "runtime":
            raise ValueError("deterministic inspection artifacts do not support agent retry")

        step = next((candidate for candidate in self.workflow().steps if "mockup-pages" in candidate.outputs), None)
        input_ids = [input_id for input_id in (step.inputs if step else []) if input_id not in ("mockup-spec",)]
        # Full mockup-spec + mockup-pages given as read-only context (ids to
        # avoid colliding with, style/navigation to match) -- same "full
        # context, narrow ask" shape as retry_screen.
        inputs: dict[str, Any] = {
            "mockup-spec": spec_artifact.content,
            "mockup-pages": pages,
            "new_screen_requirement": description,
            "existing_screen_ids": sorted(existing_ids),
            "link_from_screen_id": link_from_screen_id,
        }
        input_refs: list[ArtifactReference] = [ArtifactReference(logical_id="mockup-spec", version=spec_artifact.metadata.version)]
        for input_id in input_ids:
            try:
                upstream = self.store.artifacts.get(input_id)
            except FileNotFoundError:
                continue
            inputs[input_id] = upstream.content
            input_refs.append(ArtifactReference(logical_id=input_id, version=upstream.metadata.version))

        if mode == "split":
            scoped_instruction = (
                f"Extract this out of the existing screen '{link_from_screen_id}' (see link_from_screen_id) into "
                f"its OWN new screen: {description} "
                "The full mockup-spec and mockup-pages are included purely as read-only reference for existing "
                "style, ids, and navigation -- do not return them, and do not describe or make changes to any "
                "screen other than link_from_screen_id and your new screen. "
                "You MUST return updated_source_page: the FULL replacement HTML for link_from_screen_id with the "
                "extracted content REMOVED and replaced by a link (data-goto) to your new screen instead -- e.g. "
                "a single hardcoded record's full detail becomes a genuine multi-record list where each row links "
                "out to your new screen; a large embedded section becomes a summary/link. Do not otherwise change "
                "link_from_screen_id's remaining content, style, or other buttons/navigation. "
                "Choose a new screen `id` (snake_case) not already in existing_screen_ids, and a workflow_id or "
                "entity_id consistent with the existing screens' conventions where applicable. "
                "Return your answer as mockup-screen-addition: {screen, page, updated_source_page}."
            )
        else:
            scoped_instruction = (
                f"Add exactly ONE new screen to fill this gap in the current mockup set: {description} "
                "The full mockup-spec and mockup-pages are included purely as read-only reference for existing "
                "style, ids, and navigation -- do not return them, and do not describe or make changes to any "
                "existing screen. Choose a new screen `id` (snake_case) not already in existing_screen_ids, and a "
                "workflow_id or entity_id consistent with the existing screens' conventions where applicable."
                + (
                    f" This screen is reached from the existing screen '{link_from_screen_id}' (see "
                    "link_from_screen_id) -- if (and only if) that screen needs updating to navigate to your new "
                    "screen (e.g. a button's `data-goto` retargeted from a modal to your new screen's id), return "
                    "its full replacement HTML as `updated_source_page`; otherwise omit `updated_source_page` "
                    "entirely. Do not touch any screen other than link_from_screen_id."
                    if link_from_screen_id is not None
                    else " Do not set updated_source_page -- no existing screen was identified as needing a change."
                ) + " Return your answer as mockup-screen-addition: {screen, page, updated_source_page}."
            )

        values, generated_by = self._execute_agent(agent_id, ["mockup-screen-addition"], inputs, [], scoped_instruction)
        addition = values.get("mockup-screen-addition") or {}
        new_screen = addition.get("screen") if isinstance(addition, dict) else None
        new_page = addition.get("page") if isinstance(addition, dict) else None
        if not isinstance(new_screen, dict) or not new_screen.get("id") or not isinstance(new_page, dict) or not new_page.get("html"):
            raw = json.dumps(addition, default=str)
            if len(raw) > 500:
                raw = raw[:500] + "...(truncated)"
            raise ValueError(f"{generated_by.get('provider', 'the provider')} did not return a valid mockup-screen-addition (raw: {raw})")
        if new_screen["id"] in existing_ids:
            raise ValueError(f"the model chose screen id '{new_screen['id']}', which already exists -- retry with a more specific description")
        new_page["screen_id"] = new_screen["id"]  # guard against the model relabeling it

        updated_source = addition.get("updated_source_page") if isinstance(addition, dict) else None
        has_valid_source_patch = source_index is not None and isinstance(updated_source, dict) and updated_source.get("html")
        if mode == "split" and not has_valid_source_patch:
            raw = json.dumps(updated_source, default=str)
            raise ValueError(f"{generated_by.get('provider', 'the provider')} did not return a valid updated_source_page for '{link_from_screen_id}', which splitting a screen requires (raw: {raw})")
        if has_valid_source_patch:
            updated_source["screen_id"] = link_from_screen_id
            pages[source_index] = updated_source

        screens.append(new_screen)
        pages.append(new_page)

        saved_spec = self.store.artifacts.save(
            "mockup-spec", spec_artifact.metadata.type, {**spec_artifact.content, "screens": screens},
            generated_by=generated_by, inputs=input_refs, requirements=spec_artifact.metadata.requirements,
            parent_version=spec_artifact.metadata.version,
        )
        self.store.artifacts.update_status("mockup-spec", ArtifactStatus.SUPERSEDED, spec_artifact.metadata.version)
        saved_pages = self.store.artifacts.save(
            "mockup-pages", pages_artifact.metadata.type, pages,
            generated_by=generated_by, inputs=input_refs, requirements=pages_artifact.metadata.requirements,
            parent_version=pages_artifact.metadata.version,
        )
        self.store.artifacts.update_status("mockup-pages", ArtifactStatus.SUPERSEDED, pages_artifact.metadata.version)

        state = self.store.load_state()
        for candidate in self.workflow().steps:
            if any(output in ("mockup-spec", "mockup-pages") for output in candidate.outputs) and candidate.type == "human-approval":
                state.step_states[candidate.id] = StepStatus.PENDING
                state.workflow_status = WorkflowStatus.PAUSED
        self.store.save_state(state)

        self.store.append_event("MOCKUP_SCREEN_SPLIT" if mode == "split" else "MOCKUP_SCREEN_ADDED", artifact_id="mockup-pages", details={
            "new_screen_id": new_screen["id"], "link_from_screen_id": link_from_screen_id,
            "mockup_pages_version": saved_pages.metadata.version, "mockup_spec_version": saved_spec.metadata.version,
            "description": description,
        })
        return saved_pages

    @staticmethod
    def _coerce_mockup_page_patch(patch: Any) -> Any:
        """Recover a `mockup-page-patch` value the model nested or wrapped
        one level deeper than asked, once it's already landed under the
        right top-level key.

        `ProviderBackedAgent._recover_declared_keys` only fixes which
        top-level key the answer landed under (the "did not produce
        declared output(s)" failure); enforcing that top-level key via
        Gemini's `responseSchema` doesn't constrain the shape *inside* that
        key at all, since the envelope deliberately leaves each output's
        own fields unconstrained (see `ProviderBackedAgent._output_kind`).
        So a model can satisfy the schema with, e.g.,
        `{"mockup-page-patch": {"result": {"screen_id": ..., "html": ...}}}`
        or a single-item list -- confirmed live, the next failure after the
        top-level fix landed. Two shapes handled defensively; anything else
        falls through unchanged and the caller's own check raises with the
        raw value attached for diagnosis.
        """
        if isinstance(patch, list) and len(patch) == 1:
            patch = patch[0]
        if isinstance(patch, dict) and not patch.get("html"):
            for value in patch.values():
                if isinstance(value, dict) and value.get("html"):
                    return value
        return patch

    # ---- Direct manual edits to system-model's list fields --------------
    # For a small correction (drop a stale requirement, rename a
    # capability, add one the agent missed) it's faster and cheaper to
    # edit in place than to spend another model call. Each edit still
    # versions the artifact normally, same as an agent-produced update.

    _SYSTEM_MODEL_LIST_FIELDS = {"requirements", "business_capabilities", "business_workflows", "system_capabilities", "entities", "services", "screens", "integrations"}

    def _validate_system_model_field(self, field: str) -> None:
        if field not in self._SYSTEM_MODEL_LIST_FIELDS:
            raise ValueError(f"'{field}' is not an editable list field on system-model (editable fields: {sorted(self._SYSTEM_MODEL_LIST_FIELDS)})")

    def _owning_agent(self, artifact_id: str, current_agent: str) -> str:
        """The agent id that actually generates `artifact_id`, looked up
        fresh from the workflow step that declares it as an output --
        NOT trusted from the artifact's own possibly-already-corrupted
        `generated_by.agent` (see the bug this fixes below). Manual edits
        must preserve the real originating agent so a later retry still
        works; "runtime" is reserved for genuinely agent-less deterministic
        steps (e.g. project-inspection) and must never be written here.
        """
        for step in self.workflow().steps:
            if artifact_id in step.outputs and step.agent:
                return step.agent
        # Not a declared step output (or the step has no agent, e.g. a
        # deterministic step) -- fall back to whatever the artifact
        # already had, best-effort.
        return current_agent

    def _save_system_model_edit(self, content: dict[str, Any], field: str, action: str, detail: dict[str, Any]) -> StoredArtifact:
        current = self.store.artifacts.get("system-model")
        saved = self.store.artifacts.save(
            "system-model", current.metadata.type, content,
            generated_by={"agent": self._owning_agent("system-model", current.metadata.generated_by.agent), "provider": "manual-edit", "model": field},
            inputs=current.metadata.inputs, requirements=current.metadata.requirements,
            parent_version=current.metadata.version,
        )
        self.store.artifacts.update_status("system-model", ArtifactStatus.SUPERSEDED, current.metadata.version)
        self.store.append_event(f"SYSTEM_MODEL_ITEM_{action}", artifact_id="system-model", details={"field": field, **detail})
        return saved

    def add_system_model_item(self, field: str, value: str) -> StoredArtifact:
        self._require_initialized()
        self._validate_system_model_field(field)
        value = value.strip()
        if not value:
            raise ValueError("value cannot be empty")
        current = self.store.artifacts.get("system-model")
        content = dict(current.content)
        items = list(content.get(field) or [])
        items.append(value)
        content[field] = items
        return self._save_system_model_edit(content, field, "ADDED", {"index": len(items) - 1, "value": value})

    def edit_system_model_item(self, field: str, index: int, value: str) -> StoredArtifact:
        self._require_initialized()
        self._validate_system_model_field(field)
        value = value.strip()
        if not value:
            raise ValueError("value cannot be empty")
        current = self.store.artifacts.get("system-model")
        content = dict(current.content)
        items = list(content.get(field) or [])
        if not (0 <= index < len(items)):
            raise ValueError(f"index {index} out of range for system-model.{field} (has {len(items)} items)")
        items[index] = value
        content[field] = items
        return self._save_system_model_edit(content, field, "EDITED", {"index": index, "value": value})

    def remove_system_model_item(self, field: str, index: int) -> StoredArtifact:
        self._require_initialized()
        self._validate_system_model_field(field)
        current = self.store.artifacts.get("system-model")
        content = dict(current.content)
        items = list(content.get(field) or [])
        if not (0 <= index < len(items)):
            raise ValueError(f"index {index} out of range for system-model.{field} (has {len(items)} items)")
        removed = items.pop(index)
        content[field] = items
        return self._save_system_model_edit(content, field, "REMOVED", {"index": index, "value": removed})

    # ---- Direct manual edits to data-model -------------------------------
    # Same "edit in place, no LLM call needed for a small correction"
    # pattern as the system-model list fields above, extended one level
    # for data-model's nested entities[].fields[] / relationships[] shape.

    def _save_data_model_edit(self, content: dict[str, Any], action: str, detail: dict[str, Any]) -> StoredArtifact:
        current = self.store.artifacts.get("data-model")
        saved = self.store.artifacts.save(
            "data-model", current.metadata.type, content,
            generated_by={"agent": self._owning_agent("data-model", current.metadata.generated_by.agent), "provider": "manual-edit", "model": "data-model"},
            inputs=current.metadata.inputs, requirements=current.metadata.requirements,
            parent_version=current.metadata.version,
        )
        self.store.artifacts.update_status("data-model", ArtifactStatus.SUPERSEDED, current.metadata.version)
        self.store.append_event(f"DATA_MODEL_{action}", artifact_id="data-model", details=detail)
        return saved

    def add_data_model_entity(self, name: str, description: str = "") -> StoredArtifact:
        self._require_initialized()
        entity = DataEntity(name=name.strip(), description=description.strip()).model_dump()
        current = self.store.artifacts.get("data-model")
        content = dict(current.content)
        entities = list(content.get("entities") or [])
        entities.append(entity)
        content["entities"] = entities
        return self._save_data_model_edit(content, "ENTITY_ADDED", {"index": len(entities) - 1, "name": entity["name"]})

    def edit_data_model_entity(self, index: int, name: str, description: str = "") -> StoredArtifact:
        self._require_initialized()
        current = self.store.artifacts.get("data-model")
        content = dict(current.content)
        entities = list(content.get("entities") or [])
        if not (0 <= index < len(entities)):
            raise ValueError(f"index {index} out of range for data-model.entities (has {len(entities)} items)")
        existing = entities[index]
        updated = DataEntity(name=name.strip(), description=description.strip(), fields=existing.get("fields") or []).model_dump()
        entities[index] = updated
        content["entities"] = entities
        return self._save_data_model_edit(content, "ENTITY_EDITED", {"index": index, "name": updated["name"]})

    def remove_data_model_entity(self, index: int) -> StoredArtifact:
        self._require_initialized()
        current = self.store.artifacts.get("data-model")
        content = dict(current.content)
        entities = list(content.get("entities") or [])
        if not (0 <= index < len(entities)):
            raise ValueError(f"index {index} out of range for data-model.entities (has {len(entities)} items)")
        removed = entities.pop(index)
        content["entities"] = entities
        return self._save_data_model_edit(content, "ENTITY_REMOVED", {"index": index, "name": removed.get("name")})

    def _get_entity_or_raise(self, entities: list[dict[str, Any]], entity_index: int) -> dict[str, Any]:
        if not (0 <= entity_index < len(entities)):
            raise ValueError(f"entity index {entity_index} out of range for data-model.entities (has {len(entities)} items)")
        return entities[entity_index]

    def add_data_model_field(self, entity_index: int, name: str, type: str, description: str = "") -> StoredArtifact:
        self._require_initialized()
        current = self.store.artifacts.get("data-model")
        content = dict(current.content)
        entities = list(content.get("entities") or [])
        entity = self._get_entity_or_raise(entities, entity_index)
        field = DataField(name=name.strip(), type=type.strip(), description=description.strip()).model_dump()
        fields = list(entity.get("fields") or [])
        fields.append(field)
        entities[entity_index] = {**entity, "fields": fields}
        content["entities"] = entities
        return self._save_data_model_edit(content, "FIELD_ADDED", {"entity_index": entity_index, "field_index": len(fields) - 1, "name": field["name"]})

    def edit_data_model_field(self, entity_index: int, field_index: int, name: str, type: str, description: str = "") -> StoredArtifact:
        self._require_initialized()
        current = self.store.artifacts.get("data-model")
        content = dict(current.content)
        entities = list(content.get("entities") or [])
        entity = self._get_entity_or_raise(entities, entity_index)
        fields = list(entity.get("fields") or [])
        if not (0 <= field_index < len(fields)):
            raise ValueError(f"field index {field_index} out of range for entity {entity_index} (has {len(fields)} fields)")
        fields[field_index] = DataField(name=name.strip(), type=type.strip(), description=description.strip()).model_dump()
        entities[entity_index] = {**entity, "fields": fields}
        content["entities"] = entities
        return self._save_data_model_edit(content, "FIELD_EDITED", {"entity_index": entity_index, "field_index": field_index})

    def remove_data_model_field(self, entity_index: int, field_index: int) -> StoredArtifact:
        self._require_initialized()
        current = self.store.artifacts.get("data-model")
        content = dict(current.content)
        entities = list(content.get("entities") or [])
        entity = self._get_entity_or_raise(entities, entity_index)
        fields = list(entity.get("fields") or [])
        if not (0 <= field_index < len(fields)):
            raise ValueError(f"field index {field_index} out of range for entity {entity_index} (has {len(fields)} fields)")
        fields.pop(field_index)
        entities[entity_index] = {**entity, "fields": fields}
        content["entities"] = entities
        return self._save_data_model_edit(content, "FIELD_REMOVED", {"entity_index": entity_index, "field_index": field_index})

    def add_data_model_relationship(self, from_entity: str, to_entity: str, cardinality: str = "one-to-many", label: str = "") -> StoredArtifact:
        self._require_initialized()
        relationship = DataRelationship(from_entity=from_entity.strip(), to_entity=to_entity.strip(), cardinality=cardinality.strip() or "one-to-many", label=label.strip()).model_dump()
        current = self.store.artifacts.get("data-model")
        content = dict(current.content)
        relationships = list(content.get("relationships") or [])
        relationships.append(relationship)
        content["relationships"] = relationships
        return self._save_data_model_edit(content, "RELATIONSHIP_ADDED", {"index": len(relationships) - 1})

    def edit_data_model_relationship(self, index: int, from_entity: str, to_entity: str, cardinality: str = "one-to-many", label: str = "") -> StoredArtifact:
        self._require_initialized()
        current = self.store.artifacts.get("data-model")
        content = dict(current.content)
        relationships = list(content.get("relationships") or [])
        if not (0 <= index < len(relationships)):
            raise ValueError(f"index {index} out of range for data-model.relationships (has {len(relationships)} items)")
        relationships[index] = DataRelationship(from_entity=from_entity.strip(), to_entity=to_entity.strip(), cardinality=cardinality.strip() or "one-to-many", label=label.strip()).model_dump()
        content["relationships"] = relationships
        return self._save_data_model_edit(content, "RELATIONSHIP_EDITED", {"index": index})

    def remove_data_model_relationship(self, index: int) -> StoredArtifact:
        self._require_initialized()
        current = self.store.artifacts.get("data-model")
        content = dict(current.content)
        relationships = list(content.get("relationships") or [])
        if not (0 <= index < len(relationships)):
            raise ValueError(f"index {index} out of range for data-model.relationships (has {len(relationships)} items)")
        relationships.pop(index)
        content["relationships"] = relationships
        return self._save_data_model_edit(content, "RELATIONSHIP_REMOVED", {"index": index})

    def render_data_model_erd(self) -> str:
        """The ERD's Mermaid source, computed fresh from the current
        data-model on every call -- never itself stored or versioned, so
        it can never drift from the data it represents."""
        self._require_initialized()
        current = self.store.artifacts.get("data-model")
        return render_erd_mermaid(current.content)

    def dependencies(self, requirement_id: str) -> list[str]:
        return self.store.load_dependency_graph().requirements.get(requirement_id, [])


class RuntimeRegistry:
    """Serves one filesystem/Postgres root across many projects.

    The API layer holds one of these at startup and asks it for a runtime
    per request (`registry.for_project(project_id)`). Runtimes are cached
    by project id so we don't pay the store's construction cost per
    request, but they're cheap enough to build if the cache is cleared.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._runtimes: dict[str, DesignRuntime] = {}
        # Migrate legacy single-project layout BEFORE anything else -- if
        # the root has `.design/state/`, `.design/artifacts/`, etc.
        # directly under it (pre-multi-project), move them into
        # `.design/default/` so the existing single project stays intact
        # after the upgrade. Uses a temporary ProjectPaths purely for its
        # migration behavior; the runtime side lazily instantiates real
        # per-project runtimes below.
        from .storage import ProjectPaths, _migrate_legacy_layout
        _migrate_legacy_layout(ProjectPaths(self.root, DEFAULT_PROJECT_ID))
        self._registry = build_project_registry(self.root)

    def list_projects(self) -> list[dict[str, str]]:
        return self._registry.list_projects()

    def create_project(self, name: str) -> dict[str, str]:
        entry = self._registry.create_project(name)
        # Eagerly initialize so the fresh project immediately has agent
        # YAML, workflow YAML, and initial ProjectState on disk / in DB.
        runtime = self.for_project(entry["id"])
        runtime.initialize(entry["name"])
        return entry

    def rename_project(self, project_id: str, name: str) -> dict[str, str]:
        pid = (project_id or DEFAULT_PROJECT_ID).strip().lower()
        return self._registry.rename_project(pid, name)

    def delete_project(self, project_id: str) -> None:
        pid = (project_id or DEFAULT_PROJECT_ID).strip().lower()
        self._registry.delete_project(pid)
        self.invalidate(pid)
        # In Postgres mode, `_registry.delete_project` above only clears
        # database rows -- it has no filesystem root to act on at all. The
        # local `.design/<project_id>/` directory (agent/workflow YAML,
        # the staged BRD) exists either way, so it's removed here
        # unconditionally rather than duplicating this in both registry
        # backends. A no-op in filesystem mode, where the registry's own
        # delete_project already removed this same directory.
        design_dir = self.root / ".design" / pid
        if design_dir.exists():
            try:
                rmtree_with_retries(design_dir)
            except OSError:
                pass

    def for_project(self, project_id: str) -> DesignRuntime:
        pid = (project_id or DEFAULT_PROJECT_ID).strip().lower()
        if pid not in self._runtimes:
            self._runtimes[pid] = DesignRuntime(self.root, project_id=pid)
        return self._runtimes[pid]

    def invalidate(self, project_id: str | None = None) -> None:
        if project_id is None:
            self._runtimes.clear()
        else:
            self._runtimes.pop(project_id, None)
