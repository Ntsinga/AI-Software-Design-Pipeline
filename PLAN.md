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
- [x] Word (`.docx`) and PDF (`.pdf`) BRD text extraction through the review workspace and API
- [x] Durable task/handoff records for agent steps
- [x] Automated test suite
- [x] Opt-in OpenAI Responses and Anthropic Messages provider adapters
- [x] Live-provider generation restart with version-preserving regeneration
- [x] Opt-in Postgres-backed store (`DATABASE_URL`), for hosted deployments
- [x] Mermaid/MCP capability integration (real multi-turn tool-calling loop)
- [x] Local HTML review workspace with document upload and artifact previews
- [x] Mermaid rendering (client-side, from agent-produced Mermaid source)
- [x] Gemini provider adapter and an in-workspace provider switcher
- [x] Real HTML mockup pages, optionally styled against a captured design reference
- [x] Multi-project support: filesystem (`.design/<project_id>/`) + Postgres schema scoped by `project_id`, `RuntimeRegistry` in the API, `/projects` CRUD, in-workspace project switcher, hash-routing so refresh preserves project + active tab, legacy unprefixed API paths still forward to a `default` project
- [ ] Interactive diagram-node comments
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
10. Real tool-calling is opt-in per agent (declared in its YAML `tools:` list) and scoped to what's actually implemented in `design_pipeline/tools/registry.py` -- not a general connector framework yet. `ProviderBackedAgent` drives a bounded multi-turn loop (`DESIGN_PIPELINE_MAX_TOOL_ITERATIONS`, default 4) so a model that never stops calling tools fails clearly instead of looping forever. The one implemented tool, `mermaid.render`, needs no credentials for its core render/validate call; an optional `MERMAID_API_KEY` additionally persists diagrams to a Mermaid Chart account.

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
- 2026-08-30: Added real Mermaid MCP tool-calling: a bounded multi-turn tool-calling loop in `ProviderBackedAgent` (OpenAI and Anthropic, each with their own continuation mechanics), a `design_pipeline.tools` registry with `mermaid.render` (validate/render, no credentials required; optional `MERMAID_API_KEY` persists to a Mermaid Chart account), a new `diagrams` artifact output on the architecture step, and client-side Mermaid.js rendering in the review workspace.
- 2026-08-30: Added a Gemini provider adapter (`GeminiProvider`), including tool-calling support -- Gemini's `functionResponse` turns match by function name rather than a call id, so the adapter tracks its own synthetic id-to-name mapping across turns.
- 2026-08-30: Fixed three issues found live-testing the Gemini adapter against a real, non-trivial BRD: raised the default `max_tool_iterations` (4 -> 8; a real architecture step legitimately needs several diagram-render calls), made `diagrams` output assembled from the mermaid.render tool's own results rather than the model's retyped restatement of them (field names and even Mermaid source text were observed to drift), and fixed the Mermaid Chart account-storage response parsing (guessed shapes were wrong; corrected against the real API). Also fixed `DesignRuntime.retry()` propagating a live-provider failure as an unhandled 500 instead of the same clean error `run()`/`run_step()` already produced.
- 2026-08-30: Added a provider switcher (`PUT /provider`) to the review workspace -- rewrites `DESIGN_PIPELINE_PROVIDER` in `.env` in place, no restart needed. Also fixed a latent bug (predating today, present in all three provider adapters) where the HTTP request call itself sat outside the try/except meant to catch provider failures, so a transport-level error (timeout, connection failure) crashed as an unhandled exception instead of a clean `LiveProviderError`.
- 2026-08-31: Added real HTML mockup generation and a `design-reference` artifact the mockups step optionally styles against -- three acquisition paths sharing one artifact (`set_design_reference` for a structured/manually-captured reference, `ingest_design_reference_text`/`_bytes` to extract one from an uploaded document, `generate_design_reference` to have the live provider research a named app/system from its own knowledge). `mockup-pages` (self-contained HTML per screen) is now assembled alongside the existing `mockup-spec`, rendered in the review workspace via a sandboxed iframe.
- 2026-08-31: Added PDF (`.pdf`) document text extraction and ingestion support across the CLI, API, supporting stage references, and review workspace uploaders using `pypdf`.
