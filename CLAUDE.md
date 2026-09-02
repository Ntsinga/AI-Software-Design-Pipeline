# Working agreements for this repo

## Never point TEST_DATABASE_URL (or any test DB connection) at a real database

**What happened (2026-09-02):** While live-debugging a production data-correctness
bug, a connection string for the production Neon branch ("main") was pasted
directly into `TEST_DATABASE_URL` to run `tests/test_project_config_durability.py`
against it -- intended as a quick sanity check. That file's `engine` fixture
calls `db_metadata.drop_all(eng, checkfirst=True)` unconditionally, at both
setup AND teardown. It ran against production before the process could be
killed, wiping every row from `projects`, `artifacts`, `project_config`, and
`app_settings`. Recovered via Neon point-in-time restore (a snapshot taken
at a pre-incident timestamp, restored onto a fresh branch, verified, then
promoted over the live branch) -- but this must never happen again.

**The rule:** `TEST_DATABASE_URL` is set to a throwaway database, always,
every time, no exceptions -- that's not a suggestion in this codebase, every
Postgres-backed test file's own docstring says exactly that. Any Postgres
test file whose fixture calls `drop_all`, `TRUNCATE`, or bulk `DELETE` is
schema-destructive by design; that's fine and expected -- for a disposable
branch. It is catastrophic against anything else.

**Before running ANY command that sets `TEST_DATABASE_URL` (or otherwise
points code at a Postgres connection string) for testing or diagnostics:**

1. **Mint a fresh, disposable branch first.** Use the Neon MCP's
   `create_branch` (optionally forked from `main` if you need real data
   shaped like production, e.g. to reproduce a live bug), then
   `get_connection_string` scoped to that new `branch_id`. Never reuse
   `get_connection_string` output that wasn't just generated for a
   branch created in *this* investigation.
2. **Never paste a connection string copied from `.env`, the Render
   dashboard's env vars, or a `get_connection_string` call made without an
   explicit disposable `branch_id`** into any test invocation.
3. **Delete the disposable branch when done** (`delete_branch`) -- don't
   leave it lying around as a future foot-gun or cost.
4. **When diagnosing a live production issue, default to read-only
   tools first**: `run_sql` with `SELECT` only, or the app's own GET
   endpoints. Only reach for a full test run (or any write) against
   real data when a read-only check genuinely can't answer the
   question -- and even then, prefer replaying the exact write through
   a disposable branch seeded from a fresh snapshot of prod, not prod
   itself.
5. **Treat "this test file touches Postgres" as "this test file is
   destructive by convention" in this repo** -- actually read a
   Postgres-backed test's fixture setup (not just its name) before the
   first time you point it at any connection string, specifically
   checking for `drop_all`/`TRUNCATE`/`DELETE`.

If you are ever about to run a test suite or script against a
connection string and you are not 100% certain, from a `create_branch`
call earlier in *this same conversation*, that it is disposable --
stop and ask first.
