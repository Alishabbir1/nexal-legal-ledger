#!/usr/bin/env python3
"""
Repair or provision a portal firm tenant on the Ledger VPS.

Use after SSO 500s caused by orphaned platform records, missing workspaces,
or corrupt tenant database files.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

from _bootstrap import bootstrap_repo_root

bootstrap_repo_root()

from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.portal_link import (
    _tenant_database_is_valid,
    ensure_portal_firm_linked,
    resolve_active_portal_firm,
)


def _tenant_status(platform: PlatformDatabase, platform_firm_id: str) -> dict:
    firm = platform.get_firm(platform_firm_id)
    workspace = platform.get_workspace_for_firm(platform_firm_id)
    db_path = workspace["database_path"]
    return {
        "platform_firm_id": platform_firm_id,
        "portal_firm_id": firm.get("portal_firm_id"),
        "slug": firm.get("slug"),
        "workspace_status": workspace.get("status"),
        "database_path": db_path,
        "database_exists": os.path.exists(db_path),
        "database_valid": _tenant_database_is_valid(db_path),
        "database_size": os.path.getsize(db_path) if os.path.isfile(db_path) else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair portal firm tenant workspace/database on the ledger VPS."
    )
    parser.add_argument("--portal-firm-id", required=True, help="Portal firms.id UUID")
    parser.add_argument("--name", required=True, help="Law firm display name")
    parser.add_argument("--owner-email", required=True, help="Portal customer email")
    parser.add_argument("--portal-user-id", help="Portal firm_users.id UUID")
    parser.add_argument(
        "--subscription-tier",
        default="essential",
        help="Package tier to cache in platform + tenant DB (default: essential)",
    )
    args = parser.parse_args()

    jwt_payload = {
        "firm_id": args.portal_firm_id,
        "firm_name": args.name,
        "email": args.owner_email,
        "sub": args.portal_user_id or args.owner_email,
        "subscription_tier": args.subscription_tier,
        "role": "firm_admin",
    }

    platform = PlatformDatabase()
    before = None
    try:
        firm = ensure_portal_firm_linked(args.portal_firm_id, jwt_payload)
        before = _tenant_status(platform, firm["id"])
    except (ValueError, KeyError, sqlite3.Error, OSError) as exc:
        print(f"Pre-repair inspection failed: {exc}", file=sys.stderr)

    try:
        firm = resolve_active_portal_firm(args.portal_firm_id, jwt_payload)
    except Exception as exc:
        print(f"Repair failed: {exc}", file=sys.stderr)
        return 1

    after = _tenant_status(platform, firm["id"])
    if not after["database_valid"]:
        print(
            f"Repair incomplete: tenant database still invalid at {after['database_path']}",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "repaired": True,
                "before": before,
                "after": after,
                "firm": firm,
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
