"""Real tool implementations agents can call mid-generation.

Scoped to Mermaid only for now (see `registry.py`) -- not a general
connector framework. `sqlalchemy`/`psycopg` mirror this pattern for
Postgres: nothing here is imported unless an agent actually has a tool
available (see `runtime._execute_agent`), so the `mcp` dependency stays an
opt-in extra (`pip install ".[mermaid]"`).
"""
