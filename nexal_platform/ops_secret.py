"""
Authoritative Ledger resolution for the shared Portal ↔ Ledger ops API secret.

Portal equivalent: lib/ops-secret.ts
Env var (both sides): NEXAL_OPS_SECRET
Request header (Portal → Ledger): X-Nexal-Ops-Secret
"""
import os
from typing import Optional, Tuple

OPS_SECRET_ENV_KEYS: Tuple[str, ...] = (
    "NEXAL_OPS_SECRET",
    "LEDGER_OPS_SECRET",
    "BACKUP_HEALTH_SECRET",
)

OPS_SECRET_HEADER = "X-Nexal-Ops-Secret"

DEFAULT_ENV_FILE_PATHS: Tuple[str, ...] = (
    "/etc/nexal-ledger.env",
    "/etc/nexal/env",
    "/etc/nexal-ledger/env",
)


def _clean_secret(value: str) -> str:
    return value.strip().strip('"').strip("'").strip()


def _read_secret_from_env_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                if key.strip() in OPS_SECRET_ENV_KEYS:
                    cleaned = _clean_secret(raw)
                    if cleaned:
                        return cleaned
    except OSError:
        return None
    return None


def _read_environment_file_paths_from_systemd() -> list[str]:
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
                    if line.startswith("EnvironmentFile="):
                        env_path = line.split("=", 1)[1].strip()
                        if env_path.startswith("-"):
                            env_path = env_path[1:].strip()
                        env_path = env_path.strip('"').strip("'")
                        if env_path:
                            discovered.append(env_path)
                    elif line.startswith("Environment=") and "NEXAL_OPS_SECRET=" in line:
                        val = line.split("NEXAL_OPS_SECRET=", 1)[1]
                        val = val.split()[0].strip('"').strip("'").strip()
                        if val:
                            return [f"__inline__:{val}"]
        except OSError:
            continue
    return discovered


def _read_secret_from_service_files() -> Optional[str]:
    for entry in _read_environment_file_paths_from_systemd():
        if entry.startswith("__inline__:"):
            return entry.split(":", 1)[1]
        from_file = _read_secret_from_env_file(entry)
        if from_file:
            return from_file
    return None


def get_expected_ops_secret() -> str:
    for key in OPS_SECRET_ENV_KEYS:
        value = os.environ.get(key, "")
        cleaned = _clean_secret(value)
        if cleaned:
            return cleaned

    configured_env_file = os.environ.get("NEXAL_LEDGER_ENV_FILE", "").strip()
    env_file_candidates = []
    if configured_env_file:
        env_file_candidates.append(configured_env_file)
    env_file_candidates.extend(DEFAULT_ENV_FILE_PATHS)

    seen = set()
    for path in env_file_candidates:
        if path in seen:
            continue
        seen.add(path)
        from_file = _read_secret_from_env_file(path)
        if from_file:
            return from_file

    return _read_secret_from_service_files() or ""


def is_ops_secret_configured() -> bool:
    return bool(get_expected_ops_secret())
