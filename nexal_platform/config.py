"""
Runtime filesystem paths for Nexal Legal Ledger.

Production services must never read or write tenant/platform data under the
application source tree (e.g. /root/nexal-legal-ledger). All durable state
belongs under NEXAL_DATA_DIR (default /var/lib/nexal-legal).
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

PRODUCTION_DATA_ROOT = "/var/lib/nexal-legal"


def get_runtime_data_root() -> str:
    """Return the configured runtime data root directory."""
    env_root = os.environ.get("NEXAL_DATA_DIR", "").strip()
    if env_root:
        resolved = os.path.abspath(env_root)
        if is_forbidden_runtime_path(resolved):
            logger.error(
                "NEXAL_DATA_DIR=%s points at a forbidden deploy path; using %s instead",
                resolved,
                PRODUCTION_DATA_ROOT,
            )
            return PRODUCTION_DATA_ROOT
        return resolved

    if getattr(sys, "frozen", False):
        return os.path.abspath(
            os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "NexalLegal",
            )
        )

    # Local repo-relative data/ only when explicitly opted in — never on VPS.
    if os.environ.get("NEXAL_DEV", "").strip() == "1":
        dev_data = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
        )
        return os.path.abspath(dev_data)

    return PRODUCTION_DATA_ROOT


def is_forbidden_runtime_path(path: Optional[str]) -> bool:
    """
    True when a stored path points at the deploy user's home repo or other
    locations the nexal service user cannot access at runtime.
    """
    if not path or not str(path).strip():
        return True

    normalized = os.path.normpath(str(path)).replace("\\", "/")
    lower = normalized.lower()

    if lower.startswith("/root/"):
        return True

    repo_marker = "nexal-legal-ledger"
    if repo_marker in lower and not lower.startswith(PRODUCTION_DATA_ROOT.lower()):
        return True

    return False


def path_is_under_runtime_root(path: str, root: str) -> bool:
    """True when path resolves inside the configured runtime data root."""
    if not path or not root:
        return False
    try:
        resolved_path = os.path.abspath(path)
        resolved_root = os.path.abspath(root)
        common = os.path.commonpath([resolved_path, resolved_root])
        return common == resolved_root
    except ValueError:
        return False


@dataclass(frozen=True)
class PlatformPaths:
    """Resolved filesystem paths for platform and tenant databases."""

    root: str
    platform_db: str
    template_db: str
    tenants_dir: str

    def tenant_db_path(self, firm_id: str) -> str:
        return os.path.join(self.tenants_dir, firm_id, "solicitor_ledger.db")


def get_platform_paths(root: Optional[str] = None) -> PlatformPaths:
    """Return platform paths, creating the directory tree if needed."""
    resolved_root = os.path.abspath(root or get_runtime_data_root())
    if is_forbidden_runtime_path(resolved_root):
        logger.error(
            "Runtime data root %s is forbidden; falling back to %s",
            resolved_root,
            PRODUCTION_DATA_ROOT,
        )
        resolved_root = os.path.abspath(PRODUCTION_DATA_ROOT)
    paths = PlatformPaths(
        root=resolved_root,
        platform_db=os.path.join(resolved_root, "platform.db"),
        template_db=os.path.join(resolved_root, "templates", "solicitor_ledger.db"),
        tenants_dir=os.path.join(resolved_root, "tenants"),
    )
    safe_makedirs(os.path.dirname(paths.template_db), context="template parent dir")
    safe_makedirs(paths.tenants_dir, context="tenants dir")
    return paths


def safe_makedirs(path: str, *, context: str = "") -> None:
    """
    Create a directory only when the path is under an allowed runtime root.
    Never attempts to create paths under /root or the deploy git clone.
    """
    if not path or not str(path).strip():
        raise ValueError("safe_makedirs: empty path")
    normalized = os.path.normpath(str(path))
    if is_forbidden_runtime_path(normalized):
        suffix = f" ({context})" if context else ""
        raise PermissionError(
            f"Refusing to create directory under forbidden path{suffix}: {normalized}"
        )
    os.makedirs(normalized, exist_ok=True)


def resolve_workspace_database_path(platform, firm_id: str, stored_path: str, paths: PlatformPaths) -> str:
    """
    Ensure workspace database_path is under the runtime data root.
    Updates platform.db when a forbidden deploy-time path is detected.
    """
    canonical = paths.tenant_db_path(firm_id)
    needs_remap = (
        is_forbidden_runtime_path(stored_path)
        or not path_is_under_runtime_root(stored_path, paths.root)
    )
    if needs_remap:
        logger.warning(
            "Remapping workspace database_path for firm %s: %s -> %s",
            firm_id,
            stored_path,
            canonical,
        )
        platform.update_workspace_database_path(firm_id, canonical)
        return canonical
    return stored_path


def repair_all_stale_workspace_paths(platform) -> int:
    """
    Scan platform.db and remap every workspace.database_path that points outside
    the runtime data root (e.g. stale /root/nexal-legal-ledger deploy paths).
    """
    paths = platform.paths
    conn = platform.get_connection()
    try:
        rows = conn.execute("SELECT firm_id, database_path FROM workspaces").fetchall()
    finally:
        conn.close()

    repaired = 0
    for row in rows:
        firm_id = row["firm_id"]
        stored = row["database_path"]
        if is_forbidden_runtime_path(stored) or not path_is_under_runtime_root(stored, paths.root):
            resolve_workspace_database_path(platform, firm_id, stored, paths)
            repaired += 1
    if repaired:
        logger.warning("Repaired %d stale workspace database_path(s) at startup", repaired)
    return repaired


def require_safe_tenant_db_path(db_path: str, *, context: str = "") -> str:
    """
    Reject tenant database paths that the service user cannot access.
    Call before sqlite3.connect or os.makedirs on tenant DB paths.
    """
    if is_forbidden_runtime_path(db_path):
        suffix = f" ({context})" if context else ""
        raise PermissionError(
            "Tenant database path points at forbidden location and must be remapped"
            + suffix
            + f": {db_path}"
        )
    return db_path
