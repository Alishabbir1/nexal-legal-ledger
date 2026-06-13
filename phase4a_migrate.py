#!/usr/bin/env python3
"""
Phase 4A migration — register legacy single-tenant solicitor_ledger.db as a firm workspace.

Non-destructive by default: copies the legacy database into the tenant layout.
The original database file is never modified or deleted unless --move is passed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from typing import Optional

from database import Database, _default_db_path

from nexal_platform.config import get_platform_paths
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.template import ensure_template_database


def migrate_legacy_database(
    legacy_path: Optional[str] = None,
    firm_name: str = "Legacy Firm",
    firm_code: str = "FIRM000",
    slug: str = "legacy",
    owner_email: Optional[str] = None,
    move: bool = False,
) -> dict:
    """
    Migrate an existing single-tenant ledger database into the Phase 4A layout.

    Returns workspace registration details.
    """
    paths = get_platform_paths()
    platform = PlatformDatabase(paths)
    ensure_template_database(paths)

    legacy_path = os.path.abspath(legacy_path or _default_db_path())
    if not os.path.isfile(legacy_path):
        raise FileNotFoundError(f"Legacy database not found: {legacy_path}")

    if platform.get_firm_by_code(firm_code):
        firm = platform.get_firm_by_code(firm_code)
        workspace = platform.get_workspace_for_firm(firm["id"])
        return {
            "status": "already_migrated",
            "firm": firm,
            "workspace": workspace,
            "legacy_path": legacy_path,
        }

    firm = platform.create_firm(name=firm_name, slug=slug, firm_code=firm_code)
    firm_id = firm["id"]
    tenant_db_path = paths.tenant_db_path(firm_id)
    os.makedirs(os.path.dirname(tenant_db_path), exist_ok=True)

    if move:
        shutil.move(legacy_path, tenant_db_path)
    else:
        shutil.copy2(legacy_path, tenant_db_path)

    Database(db_path=tenant_db_path)
    workspace = platform.create_workspace(firm_id=firm_id, database_path=tenant_db_path)

    user = None
    if owner_email:
        user = platform.create_user(
            firm_id=firm_id,
            email=owner_email,
            portal_user_id=None,
        )

    return {
        "status": "migrated",
        "firm": firm,
        "workspace": workspace,
        "user": user,
        "legacy_path": legacy_path,
        "tenant_database_path": tenant_db_path,
        "move": move,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy Nexal Legal ledger database into Phase 4A multi-tenant layout."
    )
    parser.add_argument("--legacy-path", help="Path to existing solicitor_ledger.db")
    parser.add_argument("--firm-name", default="Legacy Firm")
    parser.add_argument("--firm-code", default="FIRM000")
    parser.add_argument("--slug", default="legacy")
    parser.add_argument("--owner-email")
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move legacy database instead of copying (destructive to original path).",
    )
    args = parser.parse_args()

    try:
        result = migrate_legacy_database(
            legacy_path=args.legacy_path,
            firm_name=args.firm_name,
            firm_code=args.firm_code,
            slug=args.slug,
            owner_email=args.owner_email,
            move=args.move,
        )
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    print("Phase 4A migration complete.")
    print(f"  Status: {result['status']}")
    print(f"  Firm code: {result['firm'].get('firm_code')}")
    print(f"  Workspace DB: {result['workspace']['database_path']}")
    if result["status"] == "migrated":
        print(f"  Legacy source: {result['legacy_path']}")
        print(f"  Copied/moved: {'moved' if result['move'] else 'copied'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
