"""
Ensure runtime data files are writable by the nexal-ledger service user.

Legacy migration and repair scripts often run as root on the VPS. When tenant
database files are owned by root, Gunicorn cannot INSERT/UPDATE during SSO
(provision user, password sync, set_config) and returns SSO_DB_ERROR.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Iterable, Optional

from nexal_platform.config import PlatformPaths, get_platform_paths

logger = logging.getLogger(__name__)


def _getpwnam(name: str):
    import pwd

    return pwd.getpwnam(name)


def resolve_ledger_service_user(service: Optional[str] = None) -> str:
    """Return the Unix account the nexal-ledger systemd unit runs as."""
    service = service or os.environ.get("SERVICE", "nexal-ledger")
    override = os.environ.get("NEXAL_SERVICE_USER", "").strip()
    if override:
        return override

    try:
        user = subprocess.check_output(
            ["systemctl", "show", service, "-p", "User", "--value"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if user and user not in ("", "root", "0"):
            return user
    except (OSError, subprocess.CalledProcessError):
        pass

    for candidate in ("nexal", "www-data", "ledger"):
        try:
            _getpwnam(candidate)
            return candidate
        except (KeyError, ImportError):
            continue

    return os.environ.get("SUDO_USER") or "www-data"


def _iter_runtime_paths(paths: PlatformPaths) -> Iterable[str]:
    yield paths.root
    yield paths.platform_db
    yield paths.template_db
    yield paths.tenants_dir
    if os.path.isdir(paths.tenants_dir):
        for entry in os.listdir(paths.tenants_dir):
            tenant_dir = os.path.join(paths.tenants_dir, entry)
            yield tenant_dir
            db_path = os.path.join(tenant_dir, "solicitor_ledger.db")
            if os.path.isfile(db_path):
                yield db_path
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = db_path + suffix
                if os.path.isfile(sidecar):
                    yield sidecar
    backup_root = os.path.join(paths.root, "backups")
    if os.path.isdir(backup_root):
        yield backup_root


def repair_runtime_data_ownership(
    paths: Optional[PlatformPaths] = None,
    *,
    service_user: Optional[str] = None,
    service: Optional[str] = None,
) -> dict:
    """
    chown/chmod runtime data so the ledger service user can write tenant DBs.

    Safe to run after migration, repair, or manual root intervention on the VPS.
    """
    if os.name != "posix":
        return {"skipped": True, "reason": "non-posix"}

    paths = paths or get_platform_paths()
    user = service_user or resolve_ledger_service_user(service)
    try:
        _getpwnam(user)
    except KeyError as exc:
        raise RuntimeError(f"Ledger service user does not exist: {user}") from exc
    except ImportError:
        return {"skipped": True, "reason": "non-posix", "service_user": user}

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        logger.warning(
            "Skipping ownership repair — not running as root (target user=%s)",
            user,
        )
        return {"skipped": True, "reason": "not_root", "service_user": user}

    changed: list[str] = []
    for path in sorted(set(_iter_runtime_paths(paths)), key=len):
        if not os.path.exists(path):
            continue
        shutil.chown(path, user=user)
        if os.path.isdir(path):
            os.chmod(path, 0o750)
        else:
            os.chmod(path, 0o640)
        changed.append(path)

    logger.warning(
        "Repaired runtime data ownership for service user %s (%d paths)",
        user,
        len(changed),
    )
    return {
        "skipped": False,
        "service_user": user,
        "data_root": paths.root,
        "paths_updated": len(changed),
    }
