"""
Nexal Legal – Phase 4A: Platform Database
==========================================
Manages the platform-level tenancy layer:
  - firms table      : one record per law firm
  - workspaces table : one workspace per firm

This database lives at /data/platform.db on the VPS.
It is completely separate from any firm's ledger database.

Usage:
    from platform_db import PlatformDB
    pdb = PlatformDB()
    firms = pdb.list_firms()
"""

import sqlite3
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _platform_db_path() -> str:
    """Return the path to the platform-level SQLite database."""
    base = os.environ.get(
        "NEXAL_DATA_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "platform.db")


# ---------------------------------------------------------------------------
# PlatformDB
# ---------------------------------------------------------------------------

class PlatformDB:
    """
    Platform-level database manager.
    Owns: firms, workspaces.
    Does NOT own any ledger data.
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or _platform_db_path()
        self._init_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self):
        """Create platform tables if they do not exist."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS firms (
                    firm_id     TEXT PRIMARY KEY,
                    firm_name   TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'active'
                                CHECK(status IN ('active','suspended','archived')),
                    db_path     TEXT NOT NULL,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS workspaces (
                    workspace_id    TEXT PRIMARY KEY,
                    firm_id         TEXT NOT NULL,
                    workspace_name  TEXT NOT NULL,
                    db_path         TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'active'
                                    CHECK(status IN ('active','suspended','archived')),
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (firm_id) REFERENCES firms(firm_id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_workspaces_firm_id
                    ON workspaces(firm_id);
            """)
        logger.info("PlatformDB schema initialised at %s", self.db_path)

    # ------------------------------------------------------------------
    # Firm operations
    # ------------------------------------------------------------------

    def create_firm(self, firm_id: str, firm_name: str, db_path: str,
                    status: str = "active") -> dict:
        """Insert a new firm record. Returns the created firm as a dict."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO firms (firm_id, firm_name, status, db_path,
                                      created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (firm_id, firm_name, status, db_path, now, now),
            )
        logger.info("Firm created: %s (%s)", firm_id, firm_name)
        return self.get_firm(firm_id)

    def get_firm(self, firm_id: str):
        """Return a single firm by firm_id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM firms WHERE firm_id = ?", (firm_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_firms(self, status: str = None):
        """Return all firms, optionally filtered by status."""
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM firms WHERE status = ? ORDER BY firm_name",
                    (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM firms ORDER BY firm_name"
                ).fetchall()
        return [dict(r) for r in rows]

    def update_firm_status(self, firm_id: str, status: str):
        """Update firm status (active / suspended / archived)."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE firms SET status=?, updated_at=? WHERE firm_id=?",
                (status, now, firm_id),
            )
        logger.info("Firm %s status updated to %s", firm_id, status)

    def firm_exists(self, firm_id: str) -> bool:
        return self.get_firm(firm_id) is not None

    # ------------------------------------------------------------------
    # Workspace operations
    # ------------------------------------------------------------------

    def create_workspace(self, workspace_id: str, firm_id: str,
                         workspace_name: str, db_path: str,
                         status: str = "active") -> dict:
        """Insert a new workspace record. Returns the created workspace."""
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO workspaces
                       (workspace_id, firm_id, workspace_name, db_path,
                        status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (workspace_id, firm_id, workspace_name, db_path, status,
                 now, now),
            )
        logger.info("Workspace created: %s for firm %s", workspace_id, firm_id)
        return self.get_workspace(workspace_id)

    def get_workspace(self, workspace_id: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (workspace_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_workspace_for_firm(self, firm_id: str):
        """Return the primary (first active) workspace for a firm."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM workspaces
                   WHERE firm_id=? AND status='active'
                   ORDER BY created_at LIMIT 1""",
                (firm_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_workspaces(self, firm_id: str = None):
        with self._connect() as conn:
            if firm_id:
                rows = conn.execute(
                    "SELECT * FROM workspaces WHERE firm_id=? ORDER BY created_at",
                    (firm_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workspaces ORDER BY firm_id, created_at"
                ).fetchall()
        return [dict(r) for r in rows]

    def update_workspace_status(self, workspace_id: str, status: str):
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE workspaces SET status=?, updated_at=? WHERE workspace_id=?",
                (status, now, workspace_id),
            )
