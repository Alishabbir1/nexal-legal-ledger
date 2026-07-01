#!/usr/bin/env python3
"""
Repair Serene Solicitors production SSO — align tenant path and verify sign-in.

Run on the Ledger VPS after legacy migration when /auth/sso returns SSO_DB_ERROR
or Launch Application opens an empty workspace.

  export NEXAL_DATA_DIR=/var/lib/nexal-legal
  python3 scripts/repair_serene_sso.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from _bootstrap import bootstrap_repo_root

bootstrap_repo_root()

from decimal import Decimal

from nexal_platform.config import get_platform_paths, repair_all_stale_workspace_paths
from nexal_platform.migration.legacy_tenant_import import (
    EXPECTED_APRIL_CASHBOOK,
    snapshot_tenant,
)
from nexal_platform.migration.tenant_db_relocate import (
    ensure_tenant_ready_for_sso,
    repair_firm_tenant_database_path,
    tenant_client_count,
)
from nexal_platform.platform_db import PlatformDatabase
from sso_auth import generate_sso_token

SERENE_PORTAL_FIRM_ID = "0343a4a2-5c8e-45ac-a506-61d2dde6fdb3"
SERENE_OWNER_EMAIL = "Smalik34@hotmail.co.uk"
SERENE_PORTAL_USER_ID = "df47eeee-32fc-4d63-b01e-71b784878465"
SERENE_PORTAL_CUSTOMER_ID = "0ef7eaf6-8825-49c9-901f-e727ea85c1a5"
EXPECTED_CLIENTS = 42


def _simulate_sso(platform_firm_id: str) -> dict:
    token = generate_sso_token(
        user_id=SERENE_PORTAL_USER_ID,
        email=SERENE_OWNER_EMAIL,
        firm_id=SERENE_PORTAL_FIRM_ID,
        role="firm_admin",
        username="Smalik34",
        extra={
            "firm_name": "Serene Solicitors Limited",
            "subscription_tier": "essential",
            "portal_customer_id": SERENE_PORTAL_CUSTOMER_ID,
        },
    )
    from app import app

    client = app.test_client()
    response = client.get("/auth/sso?token=" + token)
    body = response.get_data(as_text=True)
    result = {
        "status": response.status_code,
        "location": response.headers.get("Location"),
        "body_preview": body[:500],
        "success": response.status_code == 302 and response.headers.get("Location") == "/client-ledger",
    }
    if result["success"]:
        dashboard = client.get("/client-ledger")
        result["client_ledger_status"] = dashboard.status_code
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair Serene Solicitors SSO on production VPS.")
    parser.add_argument(
        "--min-clients",
        type=int,
        default=EXPECTED_CLIENTS,
        help=f"Minimum migrated client count (default: {EXPECTED_CLIENTS})",
    )
    parser.add_argument(
        "--skip-sso-simulation",
        action="store_true",
        help="Only repair paths; do not simulate /auth/sso locally.",
    )
    args = parser.parse_args()

    data_root = os.environ.get("NEXAL_DATA_DIR", "/var/lib/nexal-legal")
    os.environ.setdefault("NEXAL_DATA_DIR", data_root)

    platform = PlatformDatabase()
    firm = platform.get_firm_by_portal_firm_id(SERENE_PORTAL_FIRM_ID)
    if firm is None:
        print(
            json.dumps(
                {
                    "error": f"No platform firm linked to portal id {SERENE_PORTAL_FIRM_ID}",
                    "data_root": data_root,
                },
                indent=2,
            )
        )
        return 1

    firm_id = firm["id"]
    paths = get_platform_paths()
    repair_all_stale_workspace_paths(platform)

    workspace_before = platform.get_workspace_for_firm(firm_id)
    db_path = repair_firm_tenant_database_path(
        platform,
        firm_id,
        min_clients=args.min_clients,
        allow_global_scan=True,
    )
    db_path = ensure_tenant_ready_for_sso(platform, firm_id, min_clients=args.min_clients)
    workspace_after = platform.get_workspace_for_firm(firm_id)
    snap = snapshot_tenant(db_path)

    report = {
        "data_root": paths.root,
        "portal_firm_id": SERENE_PORTAL_FIRM_ID,
        "platform_firm_id": firm_id,
        "workspace_before": workspace_before.get("database_path"),
        "workspace_after": workspace_after.get("database_path"),
        "tenant_database_path": db_path,
        "client_count": tenant_client_count(db_path),
        "expected_clients": args.min_clients,
        "cashbook_balance": str(snap.cashbook_balance),
        "expected_cashbook_balance": str(EXPECTED_APRIL_CASHBOOK),
        "table_counts": snap.table_counts,
        "april_reconciliation": snap.april_reconciliation,
    }

    errors = []
    if report["client_count"] < args.min_clients:
        errors.append(
            f"Tenant DB has {report['client_count']} clients; expected at least {args.min_clients}"
        )
    if snap.cashbook_balance != EXPECTED_APRIL_CASHBOOK:
        errors.append(
            f"Cashbook balance {snap.cashbook_balance} != expected {EXPECTED_APRIL_CASHBOOK}"
        )

    if not args.skip_sso_simulation:
        report["sso_simulation"] = _simulate_sso(firm_id)
        if not report["sso_simulation"]["success"]:
            errors.append("SSO simulation failed — see sso_simulation in report")

    report["repair_passed"] = len(errors) == 0
    report["errors"] = errors
    print(json.dumps(report, indent=2, default=str))

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("Serene SSO repair completed successfully. Restart nexal-ledger if not already done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
