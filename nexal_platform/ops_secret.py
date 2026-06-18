"""
Shared ops API secret resolution for portal ↔ ledger health checks.
"""
import os
from typing import Optional


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
                if key.strip() in (
                    "NEXAL_OPS_SECRET",
                    "LEDGER_OPS_SECRET",
                    "BACKUP_HEALTH_SECRET",
                ):
                    cleaned = _clean_secret(raw)
                    if cleaned:
                        return cleaned
    except OSError:
        return None
    return None


def get_expected_ops_secret() -> str:
    """Resolve the configured ops secret (process env, then environment file)."""
    for key in ("NEXAL_OPS_SECRET", "LEDGER_OPS_SECRET", "BACKUP_HEALTH_SECRET"):
        value = os.environ.get(key, "")
        cleaned = _clean_secret(value)
        if cleaned:
            return cleaned

    env_file = os.environ.get("NEXAL_LEDGER_ENV_FILE", "/etc/nexal-ledger.env").strip()
    from_file = _read_secret_from_env_file(env_file)
    return from_file or ""
