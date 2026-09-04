"""Safe, local-only configuration for optional live model providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

_PROVIDER_PREFIX = {"openai": "OPENAI", "anthropic": "ANTHROPIC", "gemini": "GEMINI"}


class ProviderConfigurationError(ValueError):
    """Raised when a selected provider is not configured completely."""


@dataclass(frozen=True)
class ProviderSettings:
    provider: str = "stub"
    model: str = "deterministic-fixture"
    api_key: str | None = None
    # A single mockups-step response can now hold 30-40+ real, self-
    # contained HTML pages (one per workflow screen + entity CRUD screen) --
    # 3500 was fine for the original handful of screens but silently
    # truncated once the domain grew past ~15 entities, dropping whichever
    # workflow/screens fell near the end of the response. Same "raise as
    # the domain grows" pattern already applied to max_tool_iterations.
    max_output_tokens: int = 16000
    timeout_seconds: float = 300.0
    max_tool_iterations: int = 20

    @property
    def is_live(self) -> bool:
        return self.provider != "stub"

    @property
    def is_configured(self) -> bool:
        return not self.is_live or bool(self.api_key and self.model)

    def public_status(self) -> dict[str, object]:
        """Return settings that are safe to expose through the local API."""
        return {
            "provider": self.provider,
            "model": self.model,
            "mode": "live" if self.is_live else "deterministic",
            "configured": self.is_configured,
        }


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read a small .env subset without exporting secrets into process state."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


# ---------------------------------------------------------------------------
# Local settings file: `.design/settings.yaml`
# ---------------------------------------------------------------------------
#
# The active provider and each provider's chosen model are toggled live from
# the review UI -- that's config that changes routinely while the app runs,
# not a one-time deploy setting. `.env` stays reserved for things you set
# once by hand and rarely touch again (API keys, timeouts, token limits);
# mixing "live app state the UI rewrites on every click" into the same file
# as secrets was confusing and made `.env` diffs noisy. This file holds
# exactly that live state instead, e.g.:
#
#   provider: openai
#   models:
#     openai: gpt-5.4-nano
#     anthropic: claude-sonnet-5
#     gemini: gemini-3.5-flash-lite
#
# It sits next to the existing `projects.yaml` index (both are "app-managed,
# not hand-edited" files) rather than inside any one project's own directory,
# since the active provider/model is a deployment-wide setting, same as
# DESIGN_PIPELINE_PROVIDER always was.

def _settings_path(root: Path | str) -> Path:
    return Path(root) / ".design" / "settings.yaml"


def _read_local_settings(root: Path | str) -> dict[str, Any]:
    path = _settings_path(root)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_local_settings(root: Path | str, settings: dict[str, Any]) -> None:
    # Deferred import: storage.py imports load_database_url from this module,
    # so importing storage.atomic_write at module level here would be circular.
    from .storage import atomic_write

    atomic_write(_settings_path(root), yaml.safe_dump(settings, sort_keys=False))


def _migrate_legacy_env_settings_once(root: Path | str, file_values: dict[str, str]) -> dict[str, Any]:
    """One-time bootstrap for anyone upgrading from before this file existed.

    The very first time `.design/settings.yaml` is missing, seed it from
    whatever `DESIGN_PIPELINE_PROVIDER` / `*_MODEL` / `DESIGN_PIPELINE_MODEL`
    lines already sit in `.env`, so switching to this new settings file
    doesn't silently reset an already-configured provider/model back to
    "stub". After this seed write, `.env` is never consulted again for
    these two settings -- only this file (and, for redeploys, Postgres).
    """
    path = _settings_path(root)
    if path.exists():
        return _read_local_settings(root)
    settings: dict[str, Any] = {}
    provider = file_values.get("DESIGN_PIPELINE_PROVIDER", "").strip().lower()
    if provider in {"stub", "openai", "anthropic", "gemini"}:
        settings["provider"] = provider
    global_model = file_values.get("DESIGN_PIPELINE_MODEL", "").strip()
    models: dict[str, str] = {}
    for name, prefix in _PROVIDER_PREFIX.items():
        model = file_values.get(f"{prefix}_MODEL", "").strip()
        if not model and name == provider and global_model:
            # DESIGN_PIPELINE_MODEL used to be a cross-provider override; the
            # new per-provider `models` map has no equivalent, so the one-time
            # migration folds it into whichever provider was actually active.
            model = global_model
        if model:
            models[name] = model
    if models:
        settings["models"] = models
    if settings:
        _write_local_settings(root, settings)
    return settings


def load_provider_settings(root: Path | str, environ: Mapping[str, str] | None = None, database_url: str | None = None) -> ProviderSettings:
    """Load the active provider/model plus API keys and tuning knobs.

    Provider and model resolve from (highest precedence first): a real
    process environment variable > `.design/settings.yaml` (the live
    selection the review UI toggles) > the deployment-wide Postgres
    `app_settings` row, when `database_url` is given (restores the setting
    after an ephemeral-disk redeploy wiped the local file, e.g. Render) >
    default ("stub" / unset). `.env` is intentionally NOT part of this
    chain -- see the module docstring above `_settings_path`.

    Everything else (API keys, output-token/timeout/tool-iteration limits)
    is unrelated to this live-toggle concern and still reads from real env
    vars with `.env` as the local fallback, exactly as before.
    """
    root = Path(root)
    file_values = _read_dotenv(root / ".env")
    environment = os.environ if environ is None else environ
    settings = _migrate_legacy_env_settings_once(root, file_values)

    if database_url and "DESIGN_PIPELINE_PROVIDER" not in environment and not settings.get("provider"):
        db_provider = _db_get_provider(database_url)
        if db_provider:
            settings["provider"] = db_provider

    provider = environment.get("DESIGN_PIPELINE_PROVIDER", settings.get("provider") or "stub").strip().lower()
    if provider not in {"stub", "openai", "anthropic", "gemini"}:
        raise ProviderConfigurationError("DESIGN_PIPELINE_PROVIDER must be stub, openai, anthropic, or gemini")
    if provider == "stub":
        return ProviderSettings()

    prefix = _PROVIDER_PREFIX[provider]
    models: dict[str, str] = dict(settings.get("models") or {})
    if database_url and f"{prefix}_MODEL" not in environment and not models.get(provider):
        db_model = _db_get_model(database_url, provider)
        if db_model:
            models[provider] = db_model
    model = environment.get(f"{prefix}_MODEL", models.get(provider, "")).strip()

    def value(name: str, default: str = "") -> str:
        return environment.get(name, file_values.get(name, default)).strip()

    api_key = value(f"{prefix}_API_KEY") or None
    try:
        max_output_tokens = int(value("DESIGN_PIPELINE_MAX_OUTPUT_TOKENS", "16000"))
        timeout_seconds = float(value("DESIGN_PIPELINE_TIMEOUT_SECONDS", "300"))
        max_tool_iterations = int(value("DESIGN_PIPELINE_MAX_TOOL_ITERATIONS", "20"))
    except ValueError as exc:
        raise ProviderConfigurationError("model token, timeout, and tool-iteration settings must be numeric") from exc
    if max_output_tokens < 1 or timeout_seconds <= 0 or max_tool_iterations < 1:
        raise ProviderConfigurationError("model token, timeout, and tool-iteration settings must be positive")
    return ProviderSettings(provider, model, api_key, max_output_tokens, timeout_seconds, max_tool_iterations)


def update_provider(root: Path | str, provider: str, database_url: str | None = None) -> None:
    """Switch the active provider by updating `.design/settings.yaml`.

    Preserves whatever model is already recorded per provider. Also
    persists to Postgres when `database_url` is given, so the selection
    survives a redeploy on a host with an ephemeral filesystem (e.g.
    Render) the same way it survives a local restart via the settings file.
    """
    provider = provider.strip().lower()
    if provider not in {"stub", "openai", "anthropic", "gemini"}:
        raise ProviderConfigurationError("DESIGN_PIPELINE_PROVIDER must be stub, openai, anthropic, or gemini")
    settings = _migrate_legacy_env_settings_once(root, _read_dotenv(Path(root) / ".env"))
    settings["provider"] = provider
    _write_local_settings(root, settings)
    if database_url:
        _db_set_provider(database_url, provider)


def update_model(root: Path | str, provider: str, model: str, database_url: str | None = None) -> None:
    """Record the chosen model for one provider in `.design/settings.yaml`.

    Each provider remembers its own model independently, so switching the
    active provider and switching back doesn't lose either one's choice.
    Also persists to Postgres when `database_url` is given (see
    `update_provider`).
    """
    provider = provider.strip().lower()
    if provider not in {"openai", "anthropic", "gemini"}:
        raise ProviderConfigurationError("provider must be openai, anthropic, or gemini")
    model = model.strip()
    if not model:
        raise ProviderConfigurationError("model must not be empty")
    settings = _migrate_legacy_env_settings_once(root, _read_dotenv(Path(root) / ".env"))
    models = dict(settings.get("models") or {})
    models[provider] = model
    settings["models"] = models
    _write_local_settings(root, settings)
    if database_url:
        _db_set_model(database_url, provider, model)


def _db_get_provider(database_url: str) -> str | None:
    try:
        from .db.store import pg_get_app_setting
    except ImportError:
        return None  # optional `postgres` extra not installed
    return pg_get_app_setting(database_url, "DESIGN_PIPELINE_PROVIDER")


def _db_set_provider(database_url: str, provider: str) -> None:
    try:
        from .db.store import pg_set_app_setting
    except ImportError:
        return  # optional `postgres` extra not installed
    pg_set_app_setting(database_url, "DESIGN_PIPELINE_PROVIDER", provider)


def _db_get_model(database_url: str, provider: str) -> str | None:
    try:
        from .db.store import pg_get_app_setting
    except ImportError:
        return None  # optional `postgres` extra not installed
    return pg_get_app_setting(database_url, f"{_PROVIDER_PREFIX[provider]}_MODEL")


def _db_set_model(database_url: str, provider: str, model: str) -> None:
    try:
        from .db.store import pg_set_app_setting
    except ImportError:
        return  # optional `postgres` extra not installed
    pg_set_app_setting(database_url, f"{_PROVIDER_PREFIX[provider]}_MODEL", model)


def load_database_url(root: Path | str, environ: Mapping[str, str] | None = None) -> str | None:
    """Return `DATABASE_URL` from the real environment or project `.env`, if set.

    Absence means the caller should fall back to filesystem-backed storage;
    presence selects the optional Postgres-backed store.
    """
    file_values = _read_dotenv(Path(root) / ".env")
    environment = os.environ if environ is None else environ
    value = environment.get("DATABASE_URL", file_values.get("DATABASE_URL", "")).strip()
    return value or None


def load_mermaid_api_key(root: Path | str, environ: Mapping[str, str] | None = None) -> str | None:
    """Return `MERMAID_API_KEY`, if set.

    Purely optional: the Mermaid render/validate tool works with no key at
    all. When set, rendered diagrams are additionally persisted to the
    configured Mermaid Chart account.
    """
    file_values = _read_dotenv(Path(root) / ".env")
    environment = os.environ if environ is None else environ
    value = environment.get("MERMAID_API_KEY", file_values.get("MERMAID_API_KEY", "")).strip()
    return value or None
