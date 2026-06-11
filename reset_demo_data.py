# reset_demo_data.py
# Place this file in the same directory as app.py
#
# Usage:
#   python reset_demo_data.py
#
# WARNING: DEMO DATA RESET -- NOT FOR PRODUCTION USE

import os
import sys
import sqlite3


def _default_db_path():
    """Return the same database path that app.py / database.py uses."""
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), "SolicitorLedger"
        )
        return os.path.join(base, "solicitor_ledger.db")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "solicitor_ledger.db")


# ---------------------------------------------------------------------------
# Tables cleared in dependency order (children before parents)
# ---------------------------------------------------------------------------
TABLES_TO_CLEAR = [
    # Audit (no FK deps on operational tables)
    ("audit_trail",                 "Audit Trail"),
    ("audit_log",                   "Audit Log"),
    # Reconciliation
    ("reconciliation_bank_session", "Reconciliation Bank Session"),
    ("reconciliations",             "Reconciliations"),
    ("month_locks",                 "Month Locks"),
    # Office account
    ("office_fee_transfers",        "Office Fee Transfers"),
    ("office_cashbook",             "Office Cashbook"),
    # Client money -- children first
    ("cheque_status_log",           "Cheque Status Log"),
    ("cashbook_transactions",       "Cashbook Transactions"),
    ("ledger_transactions",         "Ledger Transactions"),
    # Clients (parent, last)
    ("clients",                     "Clients"),
]

# system_config sequence counters to reset (everything else is preserved)
RESET_CONFIG_KEYS = {
    "client_code_seq": "1",
    "txn_id_seq":      "0",
    "txn_id_year":     "0",
}


def _table_exists(conn, table):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _reset_autoincrement(conn, table):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone()
    if row:
        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))


def run_reset(db_path):
    print()
    print("=" * 60)
    print("  WARNING: DEMO DATA RESET -- NOT FOR PRODUCTION USE")
    print("=" * 60)
    print()
    print("Database : {}".format(db_path))
    print()

    if not os.path.exists(db_path):
        print("ERROR: Database file not found:\n  {}".format(db_path))
        sys.exit(1)

    answer = input("Type  YES  to proceed, anything else to abort: ").strip()
    if answer != "YES":
        print("Aborted. No changes made.")
        sys.exit(0)
    print()

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")

        total_removed = 0
        results = []

        for table, label in TABLES_TO_CLEAR:
            if not _table_exists(conn, table):
                results.append((label, table, "SKIPPED (table not found)"))
                continue
            count = conn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
            conn.execute("DELETE FROM {}".format(table))
            _reset_autoincrement(conn, table)
            total_removed += count
            results.append((label, table, "{} rows removed".format(count)))

        # Reset operational sequence counters only
        for key, val in RESET_CONFIG_KEYS.items():
            conn.execute(
                "UPDATE system_config SET value = ?, updated_date = CURRENT_TIMESTAMP WHERE key = ?",
                (val, key),
            )

        conn.execute("COMMIT")
        conn.execute("PRAGMA foreign_keys = ON")

        # Print summary
        print("  {:<35} {:<30} {}".format("Label", "Table", "Result"))
        print("  " + "-" * 75)
        for label, table, result in results:
            print("  {:<35} {:<30} {}".format(label, table, result))

        print()
        print("  Sequence counters reset : {}".format(", ".join(RESET_CONFIG_KEYS)))
        print()
        print("  Total rows removed : {}".format(total_removed))
        print()
        print("  [OK] Demo data reset completed successfully.")
        print()

    except Exception as exc:
        conn.execute("ROLLBACK")
        conn.execute("PRAGMA foreign_keys = ON")
        print()
        print("  [ERROR] Reset rolled back: {}".format(exc))
        print()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    db_path = _default_db_path()
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    run_reset(db_path)
