#!/usr/bin/env python3
"""Generic CLI: import legacy desktop DB into an existing portal-linked tenant."""
from __future__ import annotations

import argparse
import json
import sys

from _bootstrap import bootstrap_repo_root

bootstrap_repo_root()

from nexal_platform.migration.legacy_tenant_import import migrate_legacy_into_existing_tenant


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import legacy solicitor_ledger.db into an existing multi-tenant workspace."
    )
    parser.add_argument("--legacy-path", required=True)
    parser.add_argument("--portal-firm-id", required=True)
    parser.add_argument("--owner-email")
    parser.add_argument("--portal-user-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if bool(args.dry_run) == bool(args.apply):
        print("Specify exactly one of --dry-run or --apply.", file=sys.stderr)
        return 1

    try:
        result = migrate_legacy_into_existing_tenant(
            legacy_path=args.legacy_path,
            portal_firm_id=args.portal_firm_id,
            owner_email=args.owner_email,
            portal_user_id=args.portal_user_id,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(
        {
            "status": result.status,
            "validation_passed": result.validation_passed,
            "validation_errors": result.validation_errors,
            "tenant_database_path": result.tenant_database_path,
            "backup_path": result.backup_path,
            "legacy_counts": result.legacy_snapshot.table_counts,
            "migrated_counts": result.after_snapshot.table_counts,
            "legacy_cashbook_balance": str(result.legacy_snapshot.cashbook_balance),
            "migrated_cashbook_balance": str(result.after_snapshot.cashbook_balance),
        },
        indent=2,
    ))
    return 0 if result.validation_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
