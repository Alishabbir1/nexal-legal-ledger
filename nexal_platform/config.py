"""
Path configuration for Nexal Legal multi-tenant data layout.
"""
import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformPaths:
    """Resolved filesystem paths for platform and tenant databases."""

    root: str
    platform_db: str
    template_db: str
    tenants_dir: str

    def tenant_db_path(self, firm_id: str) -> str:
        return os.path.join(self.tenants_dir, firm_id, "solicitor_ledger.db")


def _default_root() -> str:
    env_root = os.environ.get("NEXAL_DATA_DIR", "").strip()
    if env_root:
        return os.path.abspath(env_root)
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "NexalLegal",
        )
    else:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return base


def get_platform_paths(root: str | None = None) -> PlatformPaths:
    """Return platform paths, creating the directory tree if needed."""
    resolved_root = os.path.abspath(root or _default_root())
    paths = PlatformPaths(
        root=resolved_root,
        platform_db=os.path.join(resolved_root, "platform.db"),
        template_db=os.path.join(resolved_root, "templates", "solicitor_ledger.db"),
        tenants_dir=os.path.join(resolved_root, "tenants"),
    )
    os.makedirs(os.path.dirname(paths.template_db), exist_ok=True)
    os.makedirs(paths.tenants_dir, exist_ok=True)
    return paths
