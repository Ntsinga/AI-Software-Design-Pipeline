"""Safe, local-only configuration for optional live model providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ProviderConfigurationError(ValueError):
    """Raised when a selected provider is not configured completely."""


@dataclass(frozen=True)
class ProviderSettings:
    provider: str = "stub"
    model: str = "deterministic-fixture"
    api_key: str | None = None
    max_output_tokens: int = 3500
    timeout_seconds: float = 60.0

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


def load_provider_settings(root: Path | str, environ: Mapping[str, str] | None = None) -> ProviderSettings:
    """Load project `.env`, with real environment variables taking precedence."""
    file_values = _read_dotenv(Path(root) / ".env")
    environment = os.environ if environ is None else environ

    def value(name: str, default: str = "") -> str:
        return environment.get(name, file_values.get(name, default)).strip()

    provider = value("DESIGN_PIPELINE_PROVIDER", "stub").lower()
    if provider not in {"stub", "openai", "anthropic"}:
        raise ProviderConfigurationError("DESIGN_PIPELINE_PROVIDER must be stub, openai, or anthropic")
    if provider == "stub":
        return ProviderSettings()

    prefix = "OPENAI" if provider == "openai" else "ANTHROPIC"
    model = value("DESIGN_PIPELINE_MODEL") or value(f"{prefix}_MODEL")
    api_key = value(f"{prefix}_API_KEY") or None
    try:
        max_output_tokens = int(value("DESIGN_PIPELINE_MAX_OUTPUT_TOKENS", "3500"))
        timeout_seconds = float(value("DESIGN_PIPELINE_TIMEOUT_SECONDS", "60"))
    except ValueError as exc:
        raise ProviderConfigurationError("model token and timeout settings must be numeric") from exc
    if max_output_tokens < 1 or timeout_seconds <= 0:
        raise ProviderConfigurationError("model token and timeout settings must be positive")
    return ProviderSettings(provider, model, api_key, max_output_tokens, timeout_seconds)


def load_database_url(root: Path | str, environ: Mapping[str, str] | None = None) -> str | None:
    """Return `DATABASE_URL` from the real environment or project `.env`, if set.

    Absence means the caller should fall back to filesystem-backed storage;
    presence selects the optional Postgres-backed store.
    """
    file_values = _read_dotenv(Path(root) / ".env")
    environment = os.environ if environ is None else environ
    value = environment.get("DATABASE_URL", file_values.get("DATABASE_URL", "")).strip()
    return value or None
