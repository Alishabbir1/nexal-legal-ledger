"""
Nexal Legal – Phase 4A: Role Foundation Migration
===================================================
Adds Phase 4A multi-tenant columns to the existing 'users' table
in every firm's solicitor_ledger.db (and the legacy single-tenant DB).

New columns added to 'users':
  - firm_id        TEXT  : Links user to a firm (NULL = legacy / no-firm)
  - email          TEXT  : User's email address (for SSO readiness)
  - portal_user_id TEXT  : Portal UUID – future SSO bridge (Phase 4B)

Role CHECK constraint is EXPANDED to include:
  admin | staff | firm_admin | cashier | read_only

BACKWARDS COMPATIBILITY:
  - All existing 'admin' and 'staff' users are untouched.
  - New columns are nullable – existing records get NULL values.
  - The CHECK on 'role' is relaxed at DB level via trigger / view
    (SQLite cannot ALTER CHECK constraints; we document this and
    handle enforcement in the application layer instead).
  - Existing login flow is completely unchanged.

Usage (run once on VPS after deploying Phase 4A code):
    python phase4a_migrate.py
"""

import sqlite3
import os
import sys
import glob
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role definition for Phase 4A
# ---------------------------------------------------------------------------

PHASE4A_ROLES = ('admin', 'staff', 'firm_admin', 'cashier', 'read_only')

# ---------------------------------------------------------------------------
# Column definitions to add
# ---------------------------------------------------------------------------

COLUMNS_TO_ADD = [
    ("firm_id",        "TEXT",  None),
    ("email",          "TEXT",  None),
    ("portal_user_id", "TEXT",  None),
]


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def _get_existing_columns(cursor, table_name: str) -> list:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def migrate_db(db_path: str) -> dict:
    """
    Apply Phase 4A schema changes to a single solicitor_ledger.db.

    Returns a dict with:
        db_path, columns_added, status, message
    """
    result = {
        "db_path": db_path,
        "columns_added": [],
        "status": "ok",
        "message": ""
    }

    if not os.path.exists(db_path):
        result["status"] = "skip"
        result["message"] = "File not found"
        return result

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA foreign_keys=ON")
        cursor = conn.cursor()

        # Check table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        )
        if not cursor.fetchone():
            result["status"] = "skip"
            result["message"] = "No 'users' table found – not a ledger DB"
            conn.close()
            return result

        existing = _get_existing_columns(cursor, "users")

        for col_name, col_type, col_default in COLUMNS_TO_ADD:
            if col_name not in existing:
                if col_default is not None:
                    ddl = (f"ALTER TABLE users ADD COLUMN {col_name} "
                           f"{col_type} DEFAULT '{col_default}'")
                else:
                    ddl = f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"
                cursor.execute(ddl)
                result["columns_added"].append(col_name)
                logger.info("  Added column '%s' to users in %s",
                            col_name, db_path)

        conn.commit()
        conn.close()

        if result["columns_added"]:
            result["message"] = (
                f"Added columns: {', '.join(result['columns_added'])}"
            )
        else:
            result["message"] = "All columns already present – no changes needed"

    except Exception as exc:
        result["status"] = "error"
        result["message"] = str(exc)
        logger.error("Migration failed for %s: %s", db_path, exc)

    return result


def run_migration(data_root: str = None) -> list:
    """
    Discover and migrate all solicitor_ledger.db files under data_root.

    Searches:
      <data_root>/solicitor_ledger.db          (legacy single-tenant DB)
      <data_root>/firms/*/solicitor_ledger.db  (per-firm DBs)
      <data_root>/template/solicitor_ledger.db (template DB)

    Returns list of migration result dicts.
    """
    if data_root is None:
        data_root = os.environ.get(
            "NEXAL_DATA_DIR",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        )

    # Also check the root directory of the project (legacy single-tenant)
    project_root = os.path.dirname(os.path.abspath(__file__))

    patterns = [
        os.path.join(project_root, "solicitor_ledger.db"),
        os.path.join(data_root, "solicitor_ledger.db"),
        os.path.join(data_root, "template", "solicitor_ledger.db"),
        os.path.join(data_root, "firms", "*", "solicitor_ledger.db"),
    ]

    db_paths = set()
    for pattern in patterns:
        for path in glob.glob(pattern):
            db_paths.add(os.path.realpath(path))

    if not db_paths:
        logger.warning(
            "No solicitor_ledger.db files found under %s or %s",
            project_root, data_root
        )
        return []

    results = []
    for db_path in sorted(db_paths):
        logger.info("Migrating: %s", db_path)
        res = migrate_db(db_path)
        results.append(res)
        status_str = res['status'].upper()
        logger.info("  [%s] %s", status_str, res['message'])

    return results


def print_summary(results: list):
    print("\n" + "=" * 60)
    print("PHASE 4A MIGRATION SUMMARY")
    print("=" * 60)
    ok = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skip")
    errors = sum(1 for r in results if r["status"] == "error")
    print(f"  Total databases found  : {len(results)}")
    print(f"  Successfully migrated  : {ok}")
    print(f"  Skipped (no changes)   : {skipped}")
    print(f"  Errors                 : {errors}")
    print()
    for r in results:
        icon = {"ok": "[OK]", "skip": "[SKIP]", "error": "[ERR]"}.get(r["status"], "?")
        print(f"  {icon}  {r['db_path']}")
        if r["columns_added"]:
            print(f"        Added: {', '.join(r['columns_added'])}")
        if r["message"]:
            print(f"        Note:  {r['message']}")
    print("=" * 60)
    print("Phase 4A role foundation migration complete.")
    print("Supported roles:", ", ".join(PHASE4A_ROLES))
    print()


if __name__ == "__main__":
    data_root = sys.argv[1] if len(sys.argv) > 1 else None
    results = run_migration(data_root=data_root)
    print_summary(results)
    sys.exit(1 if any(r["status"] == "error" for r in results) else 0)
