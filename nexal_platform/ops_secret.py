"""
Authoritative Ledger resolution for the shared Portal ↔ Ledger ops API secret.
Must stay in sync with lib/ops-secret.ts (normalization + validation).

Env var (both sides): NEXAL_OPS_SECRET
Request header (Portal → Ledger): X-Nexal-Ops-Secret
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

OPS_SECRET_ENV_KEY = "NEXAL_OPS_SECRET"
OPS_SECRET_HEADER = "X-Nexal-Ops-Secret"

DEFAULT_ENV_FILE_PATHS: tuple[str, ...] = (
    "/etc/nexal-ledger.env",
    "/etc/nexal/env",
    "/etc/nexal-ledger/env",
)

_PLACEHOLDER_VALUES = frozenset(
    {
        "replace-with-shared-secret-matching-ledger-nexal_ops_secret",
        "replace-with-shared-secret",
        "changeme",
        "change-me",
        "development",
        "dev",
        "test",
        "placeholder",
    }
)

_PLACEHOLDER_PREFIXES = (
    "replace-with",
    "change-me",
    "your-",
    "insert-",
)

_BOOTSTRAPPED = False


def normalize_ops_secret(value: Optional[str]) -> Optional[str]:
    """Identical normalization to lib/ops-secret.ts normalizeOpsSecret."""
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    return cleaned or None


def validate_ops_secret_value(value: Optional[str]) -> Optional[str]:
    """Return an error message when invalid, otherwise None."""
    normalized = normalize_ops_secret(value)
    if not normalized:
        return "NEXAL_OPS_SECRET is required and must not be blank."
    if len(normalized) < 16:
        return "NEXAL_OPS_SECRET must be at least 16 characters."
    lowered = normalized.lower()
    if lowered in _PLACEHOLDER_VALUES:
        return "NEXAL_OPS_SECRET must not use a placeholder or development value."
    if any(lowered.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES):
        return "NEXAL_OPS_SECRET must not use a placeholder or development value."
    return None


def _parse_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.isfile(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                key, raw = line.split("=", 1)
                key = key.strip()
                cleaned = normalize_ops_secret(raw)
                if cleaned:
                    values[key] = cleaned
    except OSError:
        return values
    return values


def _systemd_environment_file_paths() -> list[str]:
    service_paths = [
        "/etc/systemd/system/nexal-ledger.service",
        "/etc/systemd/system/nexal-ledger.service.d/override.conf",
    ]
    discovered: list[str] = []
    for path in service_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line.startswith("EnvironmentFile="):
                        continue
                    env_path = line.split("=", 1)[1].strip()
                    if env_path.startswith("-"):
                        env_path = env_path[1:].strip()
                    env_path = env_path.strip('"').strip("'")
                    if env_path:
                        discovered.append(env_path)
        except OSError:
            continue
    return discovered


def _candidate_env_file_paths() -> Iterable[str]:
    seen: set[str] = set()

    configured = os.environ.get("NEXAL_LEDGER_ENV_FILE", "").strip()
    if configured:
        seen.add(configured)
        yield configured

    for path in DEFAULT_ENV_FILE_PATHS:
        if path not in seen:
            seen.add(path)
            yield path

    for path in _systemd_environment_file_paths():
        if path not in seen:
            seen.add(path)
            yield path


def bootstrap_ops_secret_env() -> None:
    """Ensure os.environ[NEXAL_OPS_SECRET] is populated from the production env file."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True

    current = normalize_ops_secret(os.environ.get(OPS_SECRET_ENV_KEY))
    if current:
        os.environ[OPS_SECRET_ENV_KEY] = current
        return

    for path in _candidate_env_file_paths():
        values = _parse_env_file(path)
        secret = normalize_ops_secret(values.get(OPS_SECRET_ENV_KEY))
        if secret:
            os.environ[OPS_SECRET_ENV_KEY] = secret
            return


def get_expected_ops_secret() -> str:
    bootstrap_ops_secret_env()
    return normalize_ops_secret(os.environ.get(OPS_SECRET_ENV_KEY)) or ""


def get_provided_ops_secret(headers) -> str:
    raw = headers.get(OPS_SECRET_HEADER) if headers is not None else None
    return normalize_ops_secret(raw) or ""


def is_ops_secret_configured() -> bool:
    return validate_ops_secret_value(get_expected_ops_secret()) is None
