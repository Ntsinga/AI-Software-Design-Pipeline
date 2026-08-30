"""Optional Postgres-backed persistence, selected when `DATABASE_URL` is set.

Nothing in this package is imported unless a project actually configures a
database (see `storage.build_project_store`), so the `sqlalchemy`/`psycopg`
dependencies stay an opt-in extra (`pip install ".[postgres]"`).
"""
