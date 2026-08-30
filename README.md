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

- Upload a Word (`.docx`), Markdown, plain-text, or reStructuredText BRD.
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

## Optional OpenAI or Claude generation

The workflow engine remains the orchestrator: it decides step order, approval
gates, retries, and persistence. A selected model provider powers the three
reasoning agents (requirements, architecture, and UX).

Copy [`.env.example`](.env.example) to `.env` in the project root, then choose
one provider and fill in its key and model. `.env` is ignored by Git and keys
remain server-side; the review interface only exposes the provider name and
model, never the key.

```text
DESIGN_PIPELINE_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=your-available-model
```

```text
DESIGN_PIPELINE_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=your-available-model
```

Restart the API server after editing `.env`. Leave the provider as `stub` to
use the deterministic offline fixtures used by the test suite.

After switching to a live provider, use **Regenerate with live AI** in the
review workspace. This reruns the business, solution, system, architecture,
and mockup stages as new artifact versions while preserving the earlier stub
versions for comparison.

The MVP accepts Word (`.docx`), Markdown, plain text, or reStructuredText BRDs. You can ingest
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
