"""Production secret validation for Nexal Ledger."""
from __future__ import annotations

import os
import sys

from nexal_platform.ops_secret import validate_ops_secret_value

DEV_SSO_SECRET = "nexal-legal-dev-secret-change-in-production-2026"
DEV_FLASK_SECRET = "sra-compliant-secret-key-change-in-production"


def is_production_deploy() -> bool:
    flag = os.environ.get("NEXAL_PRODUCTION", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if os.path.isfile("/etc/nexal-ledger.env") and os.environ.get("FLASK_DEBUG", "").lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True
    return False


def _is_insecure_secret(name: str, value: str | None, dev_default: str) -> bool:
    cleaned = (value or "").strip()
    if not cleaned:
        return True
    return cleaned == dev_default


def validate_production_secrets(
    *,
    sso_secret: str | None,
    flask_secret: str | None,
    ops_secret: str | None = None,
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

    ops_issue = validate_ops_secret_value(ops_secret)
    if ops_issue:
        issues.append(f"{ops_issue} (must match Portal NEXAL_OPS_SECRET).")

    if not issues:
        return

    message = "Ledger production secret validation failed:\n" + "\n".join(
        f"  • {item}" for item in issues
    )
    print(message, file=sys.stderr)
    raise SystemExit(message)
