# Design Pipeline

Design Pipeline is a provider-neutral, requirements-driven software design
pipeline. The MVP provides a Python runtime, Pydantic contracts, declarative
YAML configuration, filesystem-backed artifacts, deterministic stub agents, a
CLI, and a small FastAPI surface.

## Quick start

```text
uv sync --extra dev
uv run design init .
uv run design ingest . path\to\requirements.md
uv run design status .
uv run design run .
uv run design artifacts .
```

To run the local review workspace and API server:

```text
uv run uvicorn design_pipeline.api:create_app --factory --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/> for the review workspace, or
<http://127.0.0.1:8000/docs> for the underlying API.

The review workspace lets you:

- Upload a Word (`.docx`), PDF, Markdown, plain-text, or reStructuredText BRD.
- Start or resume generation and see approval-gate state.
- Preview system-model traceability, architecture components, and recommended diagrams.
- Browse the generated mockup screens.
- Open artifacts to inspect versions, linked requirements, comments, approvals, and retries.

The workflow pauses at human approval gates. Approve the requested artifact and
run the workflow again:

```text
uv run design approve . system-model
uv run design run .
uv run design approve . architecture-model
uv run design run .
```

All project state and generated artifacts are stored under `.design/`.

## Optional OpenAI, Claude, or Gemini generation

The workflow engine remains the orchestrator: it decides step order, approval
gates, retries, and persistence. A selected model provider powers the three
reasoning agents (requirements, architecture, and UX). The architecture agent
can additionally call a real Mermaid MCP tool mid-generation (see below) with
any of these three live providers.

Copy [`.env.example`](.env.example) to `.env` in the project root and fill in
the API key for whichever provider(s) you'll use. `.env` is for keys only --
keys remain server-side and the review interface never exposes them.

```text
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
```

**Which provider is active, and which model each one uses, is chosen live
from the review UI** -- the provider dropdown and the model dropdown next to
it in the project header -- not by editing `.env`. That selection is stored
in `.design/settings.yaml` (app-managed, gitignored, not a secret), and each
provider remembers its own model independently, so switching providers and
switching back doesn't lose either one's choice. No server restart needed;
it takes effect on the next request.

Leave the provider on `Stub (deterministic)` to use the offline fixtures the
test suite also uses -- no key needed.

For anyone scripting against a fixed deployment, a real process environment
variable still overrides the UI selection: `DESIGN_PIPELINE_PROVIDER` and
`OPENAI_MODEL` / `ANTHROPIC_MODEL` / `GEMINI_MODEL` (set via your hosting
platform's env config, not `.env`) win over whatever's toggled in the UI.

After switching to a live provider, use **Regenerate with live AI** in the
review workspace. This reruns the business, solution, system, architecture,
and mockup stages as new artifact versions while preserving the earlier stub
versions for comparison.

## Mermaid diagram tool-calling

With a live provider selected, the architecture agent has a real `mermaid.render`
tool available: it writes Mermaid syntax, calls the tool to validate and
render it, and can react to errors before finishing -- a genuine multi-turn
tool-calling loop (`ProviderBackedAgent`), not a single-shot text call. This
needs the optional `mermaid` extra (`pip install ".[mermaid]"`) and no
credentials at all for the render/validate step itself.

Set `MERMAID_API_KEY` (from your Mermaid Chart account settings) to
additionally persist rendered diagrams to that account; leave it blank and
the diagram is still stored as this app's own versioned artifact, just not
mirrored to Mermaid Chart.

The MVP accepts Word (`.docx`), PDF, Markdown, plain text, or reStructuredText BRDs. You can ingest
a document through the CLI with `design ingest . path\to\requirements.md`, or
through `POST /documents/brd` in the API documentation. The source is stored at
`.design/input/BRD.md` and becomes the `brd` artifact when the requirements step
runs.

## Optional Postgres-backed storage

By default, project state and artifacts live under `.design/` on the local
filesystem -- the right choice for local use, and what the automated tests
run against. For a hosted deployment (Render, etc.) where the local disk is
ephemeral, set `DATABASE_URL` (in `.env` or the real environment) to a
Postgres connection string and install the optional extra:

```text
pip install ".[postgres]"
```

```text
DATABASE_URL=postgresql://user:password@host/dbname
```

With `DATABASE_URL` set, `design init`/`run`/`approve` and the API persist
everything to that database instead, and it survives restarts and
redeploys. Agent/workflow config YAML and the BRD upload staging file still
live under `.design/` on disk either way -- see `PLAN.md`'s architectural
decisions for why. Leave `DATABASE_URL` unset to keep today's filesystem
behavior.

## Development

```text
uv run pytest
uv run python -m design_pipeline --help
```

See [PLAN.md](PLAN.md) for the project-owned implementation plan and current
phase status.
