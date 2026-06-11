"""
Standalone backup script executed by the Windows Task Scheduler.
The task "Nexal Nightly Backup" runs this file daily at 03:00.

To manually register the task:
  See task_scheduler.py  (auto-registered on first app startup)

Or via schtasks:
  schtasks /create /tn "Nexal Nightly Backup"
    /tr "\"<python.exe>\" \"C:\\solicitor-web-sandbox\\run_backup.py\""
    /sc daily /st 03:00 /ru SYSTEM /rl HIGHEST /f
"""
import os
import sys

# Ensure project dir is on path
_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)
os.chdir(_dir)


def main():
    from database import Database
    from backup_service import create_backup

    db = Database()

    def audit(action: str, details: str):
        try:
            db.insert_audit_log('System', 'admin', action, 'Backup System', None, details)
        except Exception:
            pass

    success, msg, path = create_backup(db_path=db.db_path, audit_callback=audit)
    if success:
        db.set_config('last_backup_failure', '')
        print(f"Backup OK: {path}")
    else:
        from datetime import datetime
        db.set_config('last_backup_failure', datetime.now().strftime('%Y-%m-%d %H:%M') + ': ' + str(msg))
        print(f"Backup FAILED: {msg}", file=sys.stderr)
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
