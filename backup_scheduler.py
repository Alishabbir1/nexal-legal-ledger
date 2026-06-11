"""
In-process fallback scheduler for nightly backup at 03:00.
Runs when the Flask app is active. The primary backup mechanism is the
Windows Task Scheduler task ("Nexal Nightly Backup") registered by
task_scheduler.py — this daemon thread only fires if that task is absent
or has not yet run.
"""
import threading
import time
from datetime import datetime, timedelta


def _seconds_until_0300():
    now = datetime.now()
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def start_backup_scheduler(db_instance):
    """Start daemon thread that fires backup at 03:00 as an in-process fallback."""

    def _run():
        _last_run_date = None
        while True:
            try:
                secs = _seconds_until_0300()
                time.sleep(min(secs, 300))  # Wake every 5 min to re-check
                now = datetime.now()
                if now.hour != 3 or now.minute > 10:
                    continue
                if _last_run_date == now.date():
                    time.sleep(3600)
                    continue
                from backup_service import create_backup

                def audit(action, details):
                    try:
                        db_instance.insert_audit_log('System', 'admin', action, 'Backup System', None, details)
                    except Exception:
                        pass

                create_backup(db_path=db_instance.db_path, audit_callback=audit)
                _last_run_date = now.date()
                time.sleep(3600)  # Avoid re-running same night
            except Exception:
                time.sleep(3600)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
