"""
Authoritative Ledger resolution for the shared Portal ↔ Ledger ops API secret.
Must stay in sync with lib/ops-secret.ts (normalization + validation).

Env var (both sides): NEXAL_OPS_SECRET
Request header (Portal → Ledger): X-Nexal-Ops-Secret
"""
from __future__ import annotations

import glob
import os
import shlex
from typing import Iterable, Optional

OPS_SECRET_ENV_KEY = "NEXAL_OPS_SECRET"
OPS_SECRET_HEADER = "X-Nexal-Ops-Secret"

LEDGER_BOOTSTRAP_ENV_KEYS: tuple[str, ...] = (
    OPS_SECRET_ENV_KEY,
    "FLASK_SECRET_KEY",
    "SSO_SECRET_KEY",
    "NEXAL_SSO_SECRET",
)

DEV_FLASK_SECRET = "sra-compliant-secret-key-change-in-production"

DEFAULT_ENV_FILE_PATHS: tuple[str, ...] = (
    "/etc/nexal-ledger.env",
    "/etc/nexal/env",
    "/etc/nexal-ledger/env",
)

SYSTEMD_UNIT_PATHS: tuple[str, ...] = (
    "/etc/systemd/system/nexal-ledger.service",
    "/etc/systemd/system/nexal-ledger.service.d/override.conf",
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


def _parse_systemd_environment_assignment(raw: str) -> dict[str, str]:
    """Parse a systemd Environment= value (may contain multiple KEY=VAL pairs)."""
    values: dict[str, str] = {}
    text = raw.strip()
    if not text:
        return values
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    for part in parts:
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        cleaned = normalize_ops_secret(raw_value)
        if cleaned:
            values[key.strip()] = cleaned
    return values


def _systemd_unit_paths() -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for path in SYSTEMD_UNIT_PATHS:
        if path not in seen:
            seen.add(path)
            paths.append(path)
    for path in sorted(glob.glob("/etc/systemd/system/nexal-ledger.service.d/*.conf")):
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _systemd_environment_file_paths() -> list[str]:
    discovered: list[str] = []
    for path in _systemd_unit_paths():
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


def _read_systemd_service_environment() -> dict[str, str]:
    """Merge Environment= and EnvironmentFile= values from systemd unit files."""
    merged: dict[str, str] = {}
    for path in _systemd_unit_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line.startswith("Environment="):
                        merged.update(
                            _parse_systemd_environment_assignment(line.split("=", 1)[1])
                        )
        except OSError:
            continue

    for env_path in _systemd_environment_file_paths():
        merged.update(_parse_env_file(env_path))
    return merged


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


def is_usable_flask_secret(value: Optional[str]) -> bool:
    cleaned = normalize_ops_secret(value)
    if not cleaned or len(cleaned) < 16:
        return False
    return cleaned != DEV_FLASK_SECRET


def _load_flask_secret_into_environ() -> None:
    """Load FLASK_SECRET_KEY; env file wins over inline systemd dev defaults."""
    for path in _candidate_env_file_paths():
        value = normalize_ops_secret(_parse_env_file(path).get("FLASK_SECRET_KEY"))
        if is_usable_flask_secret(value):
            os.environ["FLASK_SECRET_KEY"] = value  # type: ignore[assignment]
            if not is_usable_flask_secret(os.environ.get("SECRET_KEY")):
                os.environ["SECRET_KEY"] = value
            return

    for key in ("FLASK_SECRET_KEY", "SECRET_KEY"):
        value = normalize_ops_secret(_read_systemd_service_environment().get(key))
        if is_usable_flask_secret(value):
            os.environ["FLASK_SECRET_KEY"] = value  # type: ignore[assignment]
            os.environ["SECRET_KEY"] = value
            return

    for key in ("FLASK_SECRET_KEY", "SECRET_KEY"):
        value = normalize_ops_secret(os.environ.get(key))
        if is_usable_flask_secret(value):
            os.environ["FLASK_SECRET_KEY"] = value
            os.environ["SECRET_KEY"] = value
            return

    os.environ.pop("FLASK_SECRET_KEY", None)
    if not is_usable_flask_secret(os.environ.get("SECRET_KEY")):
        os.environ.pop("SECRET_KEY", None)


def _load_env_var_into_environ(key: str) -> None:
    """Populate os.environ[key] from process env, env files, or systemd units."""
    current = normalize_ops_secret(os.environ.get(key))
    if current:
        os.environ[key] = current
        return

    for path in _candidate_env_file_paths():
        value = normalize_ops_secret(_parse_env_file(path).get(key))
        if value:
            os.environ[key] = value
            return

    value = normalize_ops_secret(_read_systemd_service_environment().get(key))
    if value:
        os.environ[key] = value


def _load_ops_secret_into_environ() -> None:
    """Populate os.environ[NEXAL_OPS_SECRET] from process env, env files, or systemd units."""
    _load_env_var_into_environ(OPS_SECRET_ENV_KEY)


def bootstrap_ledger_env() -> None:
    """Ensure production env vars are populated at application startup."""
    _load_flask_secret_into_environ()
    for key in LEDGER_BOOTSTRAP_ENV_KEYS:
        if key == "FLASK_SECRET_KEY":
            continue
        _load_env_var_into_environ(key)


def get_flask_secret() -> str:
    """Resolved Flask session secret after bootstrap (empty when unset/invalid)."""
    bootstrap_ledger_env()
    value = normalize_ops_secret(os.environ.get("FLASK_SECRET_KEY")) or normalize_ops_secret(
        os.environ.get("SECRET_KEY")
    )
    return value or ""


def bootstrap_ops_secret_env() -> None:
    """Ensure os.environ[NEXAL_OPS_SECRET] is populated at application startup."""
    bootstrap_ledger_env()


def get_expected_ops_secret() -> str:
    if not normalize_ops_secret(os.environ.get(OPS_SECRET_ENV_KEY)):
        _load_ops_secret_into_environ()
    return normalize_ops_secret(os.environ.get(OPS_SECRET_ENV_KEY)) or ""


def get_provided_ops_secret(headers) -> str:
    raw = headers.get(OPS_SECRET_HEADER) if headers is not None else None
    return normalize_ops_secret(raw) or ""


def is_ops_secret_configured() -> bool:
    return validate_ops_secret_value(get_expected_ops_secret()) is None
