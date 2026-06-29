"""Production secret validation for Nexal Ledger."""
from __future__ import annotations

import os
import sys

DEV_SSO_SECRET = "nexal-legal-dev-secret-change-in-production-2026"
DEV_FLASK_SECRET = "sra-compliant-secret-key-change-in-production"


def is_production_deploy() -> bool:
    flag = os.environ.get("NEXAL_PRODUCTION", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _is_insecure_secret(name: str, value: str | None, dev_default: str) -> bool:
    cleaned = (value or "").strip()
    if not cleaned:
        return True
    return cleaned == dev_default


def validate_production_secrets(
    *,
    sso_secret: str | None,
    flask_secret: str | None,
) -> None:
    """Refuse startup when known development secrets are used in production."""
    if not is_production_deploy():
        return

    issues: list[str] = []
    if _is_insecure_secret("SSO_SECRET_KEY", sso_secret, DEV_SSO_SECRET):
        issues.append(
            "SSO_SECRET_KEY must be set to a strong unique value (must match Portal LEDGER_SSO_SECRET)."
        )
    if _is_insecure_secret("FLASK_SECRET_KEY", flask_secret, DEV_FLASK_SECRET):
        issues.append("FLASK_SECRET_KEY (or SECRET_KEY) must be set to a strong unique value.")

    if not issues:
        return

    message = "Ledger production secret validation failed:\n" + "\n".join(
        f"  • {item}" for item in issues
    )
    print(message, file=sys.stderr)
    raise SystemExit(message)
