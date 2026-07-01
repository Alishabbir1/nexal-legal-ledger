#!/usr/bin/env python3
"""
One-time production migration: Serene Solicitors legacy desktop ledger → existing tenant.

Usage (on Ledger VPS with legacy DB uploaded):
  export NEXAL_DATA_DIR=/var/lib/nexal-legal
  python scripts/migrate_serene_production.py --legacy-path /tmp/serene_legacy.db --apply

Dry run (validates without writing):
  python scripts/migrate_serene_production.py --legacy-path /path/to/solicitor_ledger.db --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from _bootstrap import bootstrap_repo_root

bootstrap_repo_root()

from decimal import Decimal

from nexal_platform.migration.legacy_tenant_import import (
    EXPECTED_APRIL_CASHBOOK,
    migrate_legacy_into_existing_tenant,
)

SERENE_PORTAL_FIRM_ID = "0343a4a2-5c8e-45ac-a506-61d2dde6fdb3"
SERENE_FIRM_NAME = "Serene Solicitors Limited"
SERENE_OWNER_EMAIL = "Smalik34@hotmail.co.uk"
SERENE_PORTAL_USER_ID = "df47eeee-32fc-4d63-b01e-71b784878465"
SERENE_PORTAL_CUSTOMER_ID = "0ef7eaf6-8825-49c9-901f-e727ea85c1a5"
DEFAULT_DESKTOP_LEGACY = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "SolicitorLedger",
    "solicitor_ledger.db",
)


def _print_report(result) -> None:
    print(json.dumps(
        {
            "status": result.status,
            "portal_firm_id": result.portal_firm_id,
            "platform_firm_id": result.platform_firm_id,
            "tenant_database_path": result.tenant_database_path,
            "legacy_path": result.legacy_path,
            "backup_path": result.backup_path,
            "validation_passed": result.validation_passed,
            "validation_errors": result.validation_errors,
            "legacy_counts": result.legacy_snapshot.table_counts,
            "migrated_counts": result.after_snapshot.table_counts,
            "legacy_cashbook_balance": str(result.legacy_snapshot.cashbook_balance),
            "migrated_cashbook_balance": str(result.after_snapshot.cashbook_balance),
            "april_reconciliation": result.after_snapshot.april_reconciliation,
        },
        indent=2,
        default=str,
    ))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Serene Solicitors legacy desktop DB into the existing production tenant."
    )
    parser.add_argument(
        "--legacy-path",
        default=DEFAULT_DESKTOP_LEGACY,
        help=f"Path to legacy solicitor_ledger.db (default: {DEFAULT_DESKTOP_LEGACY})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate migration without modifying the tenant database.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply migration to the existing tenant (creates backup first).",
    )
    args = parser.parse_args()

    if args.dry_run and args.apply:
        print("Use either --dry-run or --apply, not both.", file=sys.stderr)
        return 1
    if not args.dry_run and not args.apply:
        print("Specify --dry-run or --apply.", file=sys.stderr)
        return 1

    try:
        result = migrate_legacy_into_existing_tenant(
            legacy_path=args.legacy_path,
            portal_firm_id=SERENE_PORTAL_FIRM_ID,
            owner_email=SERENE_OWNER_EMAIL,
            portal_user_id=SERENE_PORTAL_USER_ID,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    _print_report(result)

    april_total = None
    if result.after_snapshot.april_reconciliation:
        april_total = Decimal(str(result.after_snapshot.april_reconciliation.get("cashbook_total", "0")))

    if april_total != EXPECTED_APRIL_CASHBOOK:
        print(
            f"WARNING: April cashbook total {april_total} != expected {EXPECTED_APRIL_CASHBOOK}",
            file=sys.stderr,
        )
        if result.validation_passed:
            result.validation_passed = False
            result.validation_errors.append(
                f"April cashbook total {april_total} != expected {EXPECTED_APRIL_CASHBOOK}"
            )

    if not result.validation_passed:
        print("VALIDATION FAILED", file=sys.stderr)
        for err in result.validation_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"Serene Solicitors migration {'validated' if args.dry_run else 'completed'} successfully.")
    if args.apply:
        print("Restart nexal-ledger service, then Launch Application from the Portal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
