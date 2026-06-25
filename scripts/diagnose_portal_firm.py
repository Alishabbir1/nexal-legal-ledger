#!/usr/bin/env python3
"""Diagnose portal firm SSO on the Ledger VPS — prints tenant users and simulates SSO."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import traceback

from _bootstrap import bootstrap_repo_root

bootstrap_repo_root()

from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.portal_link import _tenant_database_is_valid
from sso_auth import generate_sso_token


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose portal firm SSO state on VPS.")
    parser.add_argument("--portal-firm-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--portal-user-id", required=True)
    parser.add_argument("--portal-customer-id")
    parser.add_argument("--firm-name", default="new")
    args = parser.parse_args()

    platform = PlatformDatabase()
    firm = platform.get_firm_by_portal_firm_id(args.portal_firm_id)
    report: dict = {"portal_firm_id": args.portal_firm_id, "linked": firm is not None}
    if not firm:
        print(json.dumps(report, indent=2))
        return 1

    report["platform_firm"] = firm
    try:
        workspace = platform.get_workspace_for_firm(firm["id"])
        report["workspace"] = workspace
        db_path = workspace["database_path"]
        report["database_valid"] = _tenant_database_is_valid(db_path)
        if _tenant_database_is_valid(db_path):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            users = conn.execute(
                "SELECT user_id, username, email, portal_user_id, role, active FROM users"
            ).fetchall()
            report["tenant_users"] = [dict(row) for row in users]
            conn.close()
    except Exception as exc:
        report["workspace_error"] = str(exc)

    token = generate_sso_token(
        user_id=args.portal_user_id,
        email=args.email,
        firm_id=args.portal_firm_id,
        role="firm_admin",
        username=args.email.split("@")[0],
        extra={
            "firm_name": args.firm_name,
            "subscription_tier": "essential",
            "portal_customer_id": args.portal_customer_id,
        },
    )

    from app import app

    client = app.test_client()
    try:
        response = client.get("/auth/sso?token=" + token)
        report["sso"] = {
            "status": response.status_code,
            "location": response.headers.get("Location"),
            "body": response.get_data(as_text=True)[:1000],
        }
        if response.status_code == 302:
            follow = client.get("/client-ledger")
            report["client_ledger"] = {
                "status": follow.status_code,
                "location": follow.headers.get("Location"),
            }
    except Exception:
        report["sso_traceback"] = traceback.format_exc()

    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("sso", {}).get("status") == 302 else 1


if __name__ == "__main__":
    raise SystemExit(main())
