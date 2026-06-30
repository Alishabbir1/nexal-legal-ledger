"""
Platform registry database — firms, workspaces, and user linkage.
"""
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nexal_platform.config import PlatformPaths, get_platform_paths


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PlatformDatabase:
    """Manages platform.db metadata for multi-tenant Nexal Legal."""

    def __init__(self, paths: Optional[PlatformPaths] = None):
        self.paths = paths or get_platform_paths()
        self.init_schema()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.paths.platform_db, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def init_schema(self) -> None:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS firms (
                    id              TEXT PRIMARY KEY,
                    firm_code       TEXT UNIQUE,
                    name            TEXT NOT NULL,
                    slug            TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    status          TEXT NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active', 'suspended', 'archived')),
                    portal_firm_id  TEXT,
                    subscription_tier TEXT NOT NULL DEFAULT 'essential',
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    id              TEXT PRIMARY KEY,
                    firm_id         TEXT NOT NULL UNIQUE REFERENCES firms(id) ON DELETE CASCADE,
                    database_path   TEXT NOT NULL UNIQUE,
                    status          TEXT NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('provisioning', 'active', 'suspended')),
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id              TEXT PRIMARY KEY,
                    firm_id         TEXT NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
                    email           TEXT NOT NULL COLLATE NOCASE,
                    portal_user_id  TEXT,
                    status          TEXT NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active', 'disabled')),
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    UNIQUE (firm_id, email)
                )
                """
            )
            self._migrate_schema(cursor)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_portal_user_id ON users(portal_user_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_firms_firm_code ON firms(firm_code)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_firms_portal_firm_id ON firms(portal_firm_id)"
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_firms_portal_firm_id_unique
                ON firms(portal_firm_id)
                WHERE portal_firm_id IS NOT NULL
                """
            )
            conn.commit()
        finally:
            conn.close()

        from nexal_platform.config import repair_all_stale_workspace_paths

        repair_all_stale_workspace_paths(self)

    def _migrate_schema(self, cursor: sqlite3.Cursor) -> None:
        """Apply incremental schema migrations without destructive changes."""
        firm_cols = {row[1] for row in cursor.execute("PRAGMA table_info(firms)").fetchall()}
        if "firm_code" not in firm_cols:
            cursor.execute("ALTER TABLE firms ADD COLUMN firm_code TEXT")
        if "subscription_tier" not in firm_cols:
            cursor.execute(
                "ALTER TABLE firms ADD COLUMN subscription_tier TEXT NOT NULL DEFAULT 'essential'"
            )

        tables = {
            row[0]
            for row in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "platform_users" in tables and "users" not in tables:
            cursor.execute("ALTER TABLE platform_users RENAME TO users")

    def create_firm(
        self,
        name: str,
        slug: str,
        firm_code: Optional[str] = None,
        portal_firm_id: Optional[str] = None,
        firm_id: Optional[str] = None,
        subscription_tier: str = "essential",
    ) -> Dict[str, Any]:
        firm_id = firm_id or str(uuid.uuid4())
        now = _utc_now()
        from lib.subscription_packages import normalize_tier

        tier = normalize_tier(subscription_tier)
        conn = self.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO firms (id, firm_code, name, slug, portal_firm_id, subscription_tier, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    firm_id,
                    firm_code.strip().upper() if firm_code else None,
                    name.strip(),
                    slug.strip().lower(),
                    portal_firm_id,
                    tier,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_firm(firm_id)

    def create_workspace(
        self,
        firm_id: str,
        database_path: str,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        workspace_id = workspace_id or str(uuid.uuid4())
        now = _utc_now()
        conn = self.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO workspaces (id, firm_id, database_path, status, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (workspace_id, firm_id, database_path, now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_workspace_for_firm(firm_id)

    def update_workspace_database_path(self, firm_id: str, database_path: str) -> Dict[str, Any]:
        """Update workspace tenant database path (e.g. after remapping forbidden paths)."""
        now = _utc_now()
        conn = self.get_connection()
        try:
            conn.execute(
                """
                UPDATE workspaces
                SET database_path = ?, updated_at = ?
                WHERE firm_id = ?
                """,
                (database_path, now, firm_id),
            )
            if conn.total_changes == 0:
                raise KeyError(f"Workspace not found for firm: {firm_id}")
            conn.commit()
        finally:
            conn.close()
        return self.get_workspace_for_firm(firm_id)

    def create_user(
        self,
        firm_id: str,
        email: str,
        portal_user_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_id = user_id or str(uuid.uuid4())
        now = _utc_now()
        conn = self.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO users (id, firm_id, email, portal_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, firm_id, email.strip().lower(), portal_user_id, now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_user(user_id)

    def update_firm_subscription_tier(self, firm_id: str, subscription_tier: str) -> Dict[str, Any]:
        """Update firm package tier (Operations Portal sync)."""
        from lib.subscription_packages import normalize_tier

        tier = normalize_tier(subscription_tier)
        now = _utc_now()
        conn = self.get_connection()
        try:
            conn.execute(
                "UPDATE firms SET subscription_tier = ?, updated_at = ? WHERE id = ?",
                (tier, now, firm_id),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_firm(firm_id)

    def update_firm_name(self, firm_id: str, name: str) -> Dict[str, Any]:
        """Update firm display name from Portal SSO (keeps platform in sync)."""
        now = _utc_now()
        conn = self.get_connection()
        try:
            conn.execute(
                "UPDATE firms SET name = ?, updated_at = ? WHERE id = ?",
                (name.strip(), now, firm_id),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_firm(firm_id)

    def update_firm_status_by_portal_firm_id(
        self,
        portal_firm_id: str,
        status: str,
    ) -> Dict[str, Any]:
        """Sync Portal account lifecycle status to platform firm + workspace."""
        allowed = {"active", "suspended", "archived"}
        normalized = (status or "").strip().lower()
        if normalized not in allowed:
            raise ValueError("Invalid ledger firm status.")

        firm = self.get_firm_by_portal_firm_id(portal_firm_id)
        if firm is None:
            raise KeyError(f"Firm not found for portal firm id: {portal_firm_id}")

        workspace_status = "active" if normalized == "active" else "suspended"
        now = _utc_now()
        conn = self.get_connection()
        try:
            conn.execute(
                "UPDATE firms SET status = ?, updated_at = ? WHERE id = ?",
                (normalized, now, firm["id"]),
            )
            conn.execute(
                "UPDATE workspaces SET status = ?, updated_at = ? WHERE firm_id = ?",
                (workspace_status, now, firm["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_firm(firm["id"])

    def get_firm(self, firm_id: str) -> Dict[str, Any]:
        conn = self.get_connection()
        try:
            row = conn.execute("SELECT * FROM firms WHERE id = ?", (firm_id,)).fetchone()
            if row is None:
                raise KeyError(f"Firm not found: {firm_id}")
            return dict(row)
        finally:
            conn.close()

    def get_firm_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM firms WHERE slug = ? COLLATE NOCASE",
                (slug.strip().lower(),),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_firm_by_code(self, firm_code: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM firms WHERE firm_code = ? COLLATE NOCASE",
                (firm_code.strip().upper(),),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_firm_by_portal_firm_id(self, portal_firm_id: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM firms WHERE portal_firm_id = ?",
                (portal_firm_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_workspace_for_firm(self, firm_id: str) -> Dict[str, Any]:
        conn = self.get_connection()
        try:
            row = conn.execute("SELECT * FROM workspaces WHERE firm_id = ?", (firm_id,)).fetchone()
            if row is None:
                raise KeyError(f"Workspace not found for firm: {firm_id}")
            workspace = dict(row)
        finally:
            conn.close()

        from nexal_platform.config import resolve_workspace_database_path

        workspace["database_path"] = resolve_workspace_database_path(
            self,
            firm_id,
            workspace["database_path"],
            self.paths,
        )
        return workspace

    def get_user(self, user_id: str) -> Dict[str, Any]:
        conn = self.get_connection()
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(f"User not found: {user_id}")
            return dict(row)
        finally:
            conn.close()

    def list_firms(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            rows = conn.execute("SELECT * FROM firms ORDER BY created_at ASC").fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    # Backwards-compatible aliases
    create_platform_user = create_user
    get_platform_user = get_user
