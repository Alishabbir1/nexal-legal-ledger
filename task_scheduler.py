"""
Windows Task Scheduler management for Nexal Nightly Backup.

Registers a daily 03:00 task running run_backup.py.
Uses UAC elevation (Start-Process -Verb RunAs) when registering so it
works from a standard (non-admin) process.
"""
import os
import sys
import subprocess
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, Any

TASK_NAME   = "Nexal Nightly Backup"
BACKUP_HOUR = 3
BACKUP_MIN  = 0
BACKUP_TIME = f"{BACKUP_HOUR:02d}:{BACKUP_MIN:02d}"   # "03:00"


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _run_ps(script: str, timeout: int = 30) -> Tuple[int, str, str]:
    """Execute a PowerShell snippet. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return -1, '', str(exc)


def _python_exe() -> str:
    return sys.executable


def _script_path() -> str:
    return os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'run_backup.py')
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def task_exists() -> bool:
    """Return True if the scheduled task is registered."""
    rc, _, _ = _run_ps(
        f'$null = Get-ScheduledTask -TaskName "{TASK_NAME}" -ErrorAction SilentlyContinue;'
        f'exit $LASTEXITCODE'
    )
    # Use schtasks /query as it works without elevation
    try:
        r = subprocess.run(
            ['schtasks', '/query', '/tn', TASK_NAME, '/fo', 'CSV'],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def get_task_status() -> Dict[str, Any]:
    """
    Return a status dictionary:
      exists      : bool or None (None = could not determine)
      state       : str   ('Ready', 'Running', 'Disabled', 'Unknown')
      last_run    : datetime or None
      next_run    : datetime or None
      last_result : int or None  (0 = success)
      run_as      : str or None
      system_level: bool
    """
    base: Dict[str, Any] = {
        'exists': None, 'state': 'Unknown',
        'last_run': None, 'next_run': None,
        'last_result': None, 'run_as': None,
        'system_level': False,
    }

    try:
        r = subprocess.run(
            ['schtasks', '/query', '/tn', TASK_NAME, '/fo', 'LIST', '/v'],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            base['exists'] = False
            return base

        base['exists'] = True
        info: Dict[str, str] = {}
        for line in r.stdout.splitlines():
            if ':' in line:
                k, _, v = line.partition(':')
                info[k.strip()] = v.strip()

        # Parse state
        for k in info:
            if 'status' in k.lower() and 'last' not in k.lower():
                base['state'] = info[k]
                break

        # Parse run-as user
        for k in info:
            if 'run as' in k.lower() or 'task to run' in k.lower():
                pass
            if 'run as user' in k.lower():
                base['run_as'] = info[k]
                base['system_level'] = info[k].upper() in ('SYSTEM', 'NT AUTHORITY\\SYSTEM')
                break

        # Parse next run time
        date_formats = ['%d/%m/%Y %H:%M:%S', '%m/%d/%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S']
        for k in info:
            if 'next run time' in k.lower():
                for fmt in date_formats:
                    try:
                        base['next_run'] = datetime.strptime(info[k], fmt)
                        break
                    except ValueError:
                        pass
                break

        # Parse last run time
        for k in info:
            if 'last run time' in k.lower():
                for fmt in date_formats:
                    try:
                        dt = datetime.strptime(info[k], fmt)
                        # Filter Windows "never run" sentinel dates (pre-2000)
                        if dt.year >= 2000:
                            base['last_run'] = dt
                        break
                    except ValueError:
                        pass
                break

        # Parse last result
        for k in info:
            if 'last result' in k.lower():
                try:
                    base['last_result'] = int(info[k])
                except (ValueError, TypeError):
                    pass
                break

    except Exception:
        pass

    # Fallback: calculate next_run from schedule
    if base['exists'] and base['next_run'] is None:
        now    = datetime.now()
        target = now.replace(hour=BACKUP_HOUR, minute=BACKUP_MIN,
                             second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        base['next_run'] = target

    return base


def create_task(python_exe: str = None,
                script: str    = None) -> Tuple[bool, str, bool]:
    """
    Register the scheduled task via UAC-elevated schtasks.

    Uses Start-Process -Verb RunAs so it can register from a non-admin
    process (triggers a UAC prompt).  Falls back to a non-elevated attempt
    which works if the process already has admin rights.

    Returns (success, message, is_system_level).
    """
    py  = os.path.normpath(python_exe or _python_exe())
    scr = script or _script_path()

    # Build schtasks argument string (inner quotes escaped for the shell)
    task_cmd = (
        f'/create /tn "{TASK_NAME}" '
        f'/tr "\\"{py}\\" \\"{scr}\\"" '
        f'/sc daily /st {BACKUP_TIME} /rl HIGHEST /f'
    )

    # ── Attempt 1: elevated via UAC (non-admin session) ──
    ps_elevated = f"""
$p = Start-Process schtasks -ArgumentList '{task_cmd}' -Verb RunAs -Wait -PassThru -WindowStyle Hidden 2>$null
if ($p -and $p.ExitCode -eq 0) {{ Write-Output "OK_ELEVATED" }} else {{ Write-Output "FAIL_$($p.ExitCode)" }}
"""
    rc, out, _ = _run_ps(ps_elevated, timeout=40)
    if rc == 0 and 'OK_ELEVATED' in out:
        return (True,
                f'Task "{TASK_NAME}" registered — runs daily at {BACKUP_TIME}.',
                False)

    # ── Attempt 2: direct schtasks (already-elevated session) ──
    try:
        args = [
            'schtasks', '/create',
            '/tn', TASK_NAME,
            '/tr', f'"{py}" "{scr}"',
            '/sc', 'daily',
            '/st', BACKUP_TIME,
            '/rl', 'HIGHEST',
            '/f',
        ]
        r2 = subprocess.run(args, capture_output=True, text=True, timeout=20)
        if r2.returncode == 0:
            return (True,
                    f'Task "{TASK_NAME}" registered — runs daily at {BACKUP_TIME}.',
                    False)
        err = r2.stderr.strip() or r2.stdout.strip()
    except Exception as exc:
        err = str(exc)

    return False, f'Could not register scheduled task: {err}', False


def delete_task() -> Tuple[bool, str]:
    """Unregister the task via UAC elevation (silently succeeds if absent)."""
    ps = f"""
$p = Start-Process schtasks -ArgumentList '/delete /tn "{TASK_NAME}" /f' `
     -Verb RunAs -Wait -PassThru -WindowStyle Hidden 2>$null
if ($p -and $p.ExitCode -eq 0) {{ Write-Output "OK" }} else {{ Write-Output "FAIL_$($p.ExitCode)" }}
"""
    rc, out, _ = _run_ps(ps, timeout=30)
    if rc == 0 and 'OK' in out:
        return True, 'Task deleted.'
    # Fallback: direct call (already-elevated session)
    try:
        r = subprocess.run(
            ['schtasks', '/delete', '/tn', TASK_NAME, '/f'],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
    except Exception as exc:
        return False, str(exc)


def repair_task(python_exe: str = None,
                script: str    = None) -> Tuple[bool, str, bool]:
    """Delete the task (if present) and re-register from scratch."""
    delete_task()
    return create_task(python_exe, script)


def ensure_task_registered(audit_callback=None) -> bool:
    """
    Idempotent bootstrap: register task only if it does not exist yet.
    Returns True if the task is ready after the call.
    """
    if task_exists():
        return True
    success, msg, _ = create_task()
    if audit_callback:
        action = 'BACKUP_TASK_CREATED' if success else 'BACKUP_TASK_CREATE_FAILED'
        audit_callback(action, msg)
    return success
