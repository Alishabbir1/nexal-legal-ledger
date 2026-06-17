"""
Standalone backup entry — delegates to Phase 5.4 multi-tenant backup service.
"""
import os
import sys

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)
os.chdir(_dir)


def main():
    from nexal_platform.backup import BackupService

    schedule = os.environ.get("NEXAL_BACKUP_SCHEDULE", "daily").strip().lower()
    if schedule not in ("daily", "weekly", "monthly"):
        schedule = "daily"

    service = BackupService()
    result = service.run_backup(schedule=schedule)
    if result.success:
        print(f"Backup OK: {result.manifest_path}")
        return 0

    print(f"Backup FAILED: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
