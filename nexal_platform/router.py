"""
Database routing — resolve firm to isolated SQLite ledger database.
"""
from typing import Any, Dict, Optional, Tuple

from database import Database

from nexal_platform.config import PlatformPaths, get_platform_paths, resolve_workspace_database_path
from nexal_platform.platform_db import PlatformDatabase


class TenantRouter:
    """Routes a firm identifier to its dedicated ledger Database instance."""

    def __init__(self, paths: Optional[PlatformPaths] = None):
        self.paths = paths or get_platform_paths()
        self.platform = PlatformDatabase(self.paths)
        self._cache: Dict[str, Database] = {}

    def resolve_database_path(self, firm_id: str) -> str:
        workspace = self.platform.get_workspace_for_firm(firm_id)
        if workspace["status"] != "active":
            raise PermissionError(f"Workspace is not active for firm: {firm_id}")
        return resolve_workspace_database_path(
            self.platform,
            firm_id,
            workspace["database_path"],
            self.paths,
        )

    def get_database(self, firm_id: str) -> Database:
        """Return a Database instance bound to the firm's isolated ledger database."""
        if firm_id in self._cache:
            return self._cache[firm_id]

        db_path = self.resolve_database_path(firm_id)
        db = Database(db_path=db_path)
        self._cache[firm_id] = db
        return db

    def clear_cache(self) -> None:
        self._cache.clear()

    def get_database_for_code(self, firm_code: str) -> Tuple[Dict[str, Any], Database]:
        firm = self.platform.get_firm_by_code(firm_code)
        if firm is None:
            raise KeyError(f"No firm registered for code: {firm_code}")
        return firm, self.get_database(firm["id"])

    def get_database_for_slug(self, slug: str) -> Tuple[Dict[str, Any], Database]:
        firm = self.platform.get_firm_by_slug(slug)
        if firm is None:
            raise KeyError(f"No firm registered for slug: {slug}")
        return firm, self.get_database(firm["id"])
