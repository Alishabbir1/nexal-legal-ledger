#!/usr/bin/env python3
"""
Run multi-tenant backups for Nexal Legal Ledger.

Usage:
  python scripts/backup_all.py                  # daily (default)
  python scripts/backup_all.py --schedule weekly
  python scripts/backup_all.py --schedule monthly
  python scripts/backup_all.py --all-schedules  # daily + weekly + monthly

Environment:
  NEXAL_DATA_DIR=/var/lib/nexal-legal
  NEXAL_BACKUP_DIR=/var/lib/nexal-legal/backups  (optional)
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nexal Legal multi-tenant backup")
    parser.add_argument(
        "--schedule",
        choices=["daily", "weekly", "monthly"],
        default="daily",
        help="Backup retention tier (default: daily)",
    )
    parser.add_argument(
        "--all-schedules",
        action="store_true",
        help="Run daily, weekly, and monthly backups in one invocation",
    )
    args = parser.parse_args()

    from nexal_platform.backup import BackupService

    service = BackupService()
    schedules = ["daily", "weekly", "monthly"] if args.all_schedules else [args.schedule]
    exit_code = 0

    for schedule in schedules:
        result = service.run_backup(schedule=schedule)
        label = "OK" if result.success else "FAILED"
        print(f"[{label}] {schedule} backup run_id={result.run_id} manifest={result.manifest_path}")
        for entry in result.entries:
            status = "ok" if entry.success else f"error={entry.error}"
            name = entry.firm_name or entry.target_type
            print(f"  - {name}: {status}")
        if not result.success:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
