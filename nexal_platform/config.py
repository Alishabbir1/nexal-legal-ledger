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
        return os.path.abspath(env_root)

    if getattr(sys, "frozen", False):
        return os.path.abspath(
            os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "NexalLegal",
            )
        )

    dev_data = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
    )
    dev_parent = os.path.dirname(dev_data)
    try:
        if os.path.isdir(dev_parent) and os.access(dev_parent, os.W_OK):
            return os.path.abspath(dev_data)
    except OSError:
        pass

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
    paths = PlatformPaths(
        root=resolved_root,
        platform_db=os.path.join(resolved_root, "platform.db"),
        template_db=os.path.join(resolved_root, "templates", "solicitor_ledger.db"),
        tenants_dir=os.path.join(resolved_root, "tenants"),
    )
    os.makedirs(os.path.dirname(paths.template_db), exist_ok=True)
    os.makedirs(paths.tenants_dir, exist_ok=True)
    return paths


def resolve_workspace_database_path(platform, firm_id: str, stored_path: str, paths: PlatformPaths) -> str:
    """
    Ensure workspace database_path is under the runtime data root.
    Updates platform.db when a forbidden deploy-time path is detected.
    """
    canonical = paths.tenant_db_path(firm_id)
    if is_forbidden_runtime_path(stored_path):
        logger.warning(
            "Remapping forbidden workspace database_path for firm %s: %s -> %s",
            firm_id,
            stored_path,
            canonical,
        )
        platform.update_workspace_database_path(firm_id, canonical)
        return canonical
    return stored_path
