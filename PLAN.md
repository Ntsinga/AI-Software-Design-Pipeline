# Design Pipeline Project Plan

This file is the project-owned implementation plan. The source concept plan
defines the architecture; this file records the executable MVP scope and its
progress.

## Current phase: MVP foundation

- [x] Python package and dependency configuration
- [x] Pydantic domain contracts
- [x] Filesystem project initialization and artifact registry
- [x] Declarative agent and workflow loading
- [x] Deterministic stub agents
- [x] Workflow engine with approval gates and retries
- [x] CLI commands
- [x] FastAPI skeleton
- [x] Text BRD ingestion through CLI and API
- [x] Word (`.docx`) BRD text extraction through the review workspace and API
- [x] Durable task/handoff records for agent steps
- [x] Automated test suite
- [x] Opt-in OpenAI Responses and Anthropic Messages provider adapters
- [x] Live-provider generation restart with version-preserving regeneration
- [x] Opt-in Postgres-backed store (`DATABASE_URL`), for hosted deployments
- [ ] Mermaid/MCP capability integration
- [x] Local HTML review workspace with document upload and artifact previews
- [ ] Mermaid rendering and interactive diagram-node comments
- [ ] Incremental BRD impact propagation
- [ ] Harness-specific agent adapters

## MVP success criteria

A newly initialized project can execute the complete deterministic workflow,
persist artifacts and execution history under `.design/`, pause at approval
gates, resume after approvals, and retry one artifact without regenerating
unrelated approved artifacts.

## Architectural decisions

1. Workflow orchestration is application code, not an autonomous supervisor.
2. Agents are provider-neutral; deterministic stubs are the default and live providers are opt-in through a server-side `.env`.
3. Pydantic models define contracts between runtime components.
4. Filesystem state is canonical and Git-friendly by default; a Postgres-backed store is an opt-in alternative for hosted deployments (see decision 9), selected by `DATABASE_URL` with no other code changes.
5. The CLI and FastAPI layer share one runtime and storage implementation.
6. Artifacts are versioned and carry their inputs, requirements, and lifecycle.
7. Human approval is an explicit workflow state.
8. Comments and approvals are durable structured records.
9. `storage.build_project_store` is the single place that chooses between the filesystem and Postgres stores, both of which implement the same public surface; `DesignRuntime` is the only caller. Agent/workflow config YAML and the BRD upload staging file stay on the local filesystem regardless of backend -- they're small, human-editable config and a transient pre-ingestion staging step, not state that needs to survive a redeploy on its own.

## Deferred capabilities

Mermaid rendering, external MCP clients, React mockups,
continuous file watching, automatic selective regeneration, production review
hosting, and vendor-specific agent adapters remain outside the MVP.

## Change log

- 2026-08-28: Created the project-owned plan while implementing the Python MVP.
- 2026-08-29: Added text BRD ingestion, custom requirement traceability, and durable agent task/handoff records.
- 2026-08-30: Added the local review workspace for document upload, artifact previews, system/architecture charts, mockup navigation, and review actions.
- 2026-08-30: Added optional OpenAI and Anthropic adapters with project-local, Git-ignored credential configuration.
- 2026-08-30: Added Word document ingestion that extracts BRD text locally before generation.
- 2026-08-30: Added live-provider regeneration from the review workspace, preserving prior artifact versions.
- 2026-08-30: Added an opt-in Postgres-backed store (`design_pipeline.db`, selected by `DATABASE_URL`) alongside the existing filesystem store, ahead of hosting on Render with a Neon-managed database.
