"""
Database layer - Copy from ui/database.py or use shared version
"""
import sqlite3
import json
import time
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from decimal import Decimal, ROUND_HALF_UP
import os
import logging
import calendar
from werkzeug.security import generate_password_hash

logger = logging.getLogger(__name__)

DB_TIMEOUT = 30
DB_MAX_RETRIES = 5
DB_RETRY_DELAY = 0.2
DB_WRITE_RETRIES = 3
DB_WRITE_RETRY_DELAY = 0.5

# Rows with reversal_depth % 2 == 0 count toward client ledger / client cashbook balances.
# Original postings: depth 0. First compensator: 1 (excluded). Re-reversal: 2 (included), etc.
_LEDGER_BALANCE_EFFECTIVE_SQL = """
              AND COALESCE(lt.is_deleted, 0) = 0
              AND (lt.reversal_status IS NULL OR lt.reversal_status != 'REVERSED')
              AND (COALESCE(lt.reversal_depth, 0) % 2 = 0)
"""
_CASHBOOK_BALANCE_EFFECTIVE_SQL = """
              AND COALESCE(is_deleted, 0) = 0
              AND (reversal_status IS NULL OR reversal_status != 'REVERSED')
              AND (COALESCE(reversal_depth, 0) % 2 = 0)
"""
# Same rules when cashbook is aliased as cb (JOIN to ledger)
_CASHBOOK_BALANCE_EFFECTIVE_SQL_CB = """
              AND COALESCE(cb.is_deleted, 0) = 0
              AND (cb.reversal_status IS NULL OR cb.reversal_status != 'REVERSED')
              AND (COALESCE(cb.reversal_depth, 0) % 2 = 0)
"""


def _ledger_row_counts_toward_running_balance(row: dict) -> bool:
    """Same inclusion rules as client ledger running total (cleared / reversal parity). Read-only helper."""
    linked = row.get('linked_cashbook_id')
    if linked is not None and (row.get('cashbook_status') or '') != 'Cleared':
        return False
    rs = row.get('reversal_status') or 'ACTIVE'
    if rs == 'REVERSED':
        return False
    if int(row.get('reversal_depth') or 0) % 2 != 0:
        return False
    return True


def _ledger_row_signed_delta_for_running(row: dict) -> Decimal:
    """Signed contribution toward running balance for one ledger row (0 if excluded)."""
    if not _ledger_row_counts_toward_running_balance(row):
        return Decimal('0.00')
    amt = Decimal(str(row['amount']))
    tt = row.get('transaction_type') or ''
    if tt == 'Receipt':
        return amt
    if tt in ('Payment', 'Transfer'):
        return -amt
    return Decimal('0.00')


def _log_db_retry(operation: str, attempt: int, error: Exception):
    """Log when a database retry occurs."""
    import sys
    print(f"[DB] Retry {attempt}/{DB_WRITE_RETRIES} for {operation}: {error}", file=sys.stderr)


def _get_data_dir() -> str:
    """Return the persistent runtime data directory for the application."""
    from nexal_platform.config import get_runtime_data_root

    base = get_runtime_data_root()
    os.makedirs(base, exist_ok=True)
    return base


def _default_db_path() -> str:
    """Return persistent database path."""
    return os.path.join(_get_data_dir(), 'solicitor_ledger.db')


class Database:
    """Database manager with compliance rule enforcement"""
    
    def __init__(self, db_path: str = None, is_template: bool = False, skip_user_seed: bool = False):
        if db_path is None:
            db_path = _default_db_path()
        else:
            from nexal_platform.config import require_safe_tenant_db_path

            db_path = require_safe_tenant_db_path(db_path, context="Database.__init__")
        self.db_path = db_path
        self.is_template = is_template
        self.skip_user_seed = skip_user_seed
        self.init_database()
    
    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=DB_TIMEOUT,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")  # 30 seconds
        return conn

    def _execute_with_retry(self, conn, cursor, execute_fn, *args, **kwargs):
        """Execute with retry on database locked."""
        last_err = None
        for attempt in range(DB_MAX_RETRIES):
            try:
                return execute_fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    time.sleep(DB_RETRY_DELAY * (attempt + 1))
                else:
                    raise
        raise last_err
    
    def init_database(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_code TEXT NOT NULL UNIQUE,
                client_name TEXT NOT NULL,
                matter_reference TEXT,
                description TEXT,
                matter_status TEXT DEFAULT 'OPEN' CHECK(matter_status IN ('OPEN', 'CLOSED')),
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                CHECK(client_code != '' AND client_name != '')
            )
        """)
        self._ensure_column(cursor, 'clients', 'matter_status', "TEXT DEFAULT 'OPEN'")
        self._ensure_column(cursor, 'clients', 'address', "TEXT")
        self._ensure_column(cursor, 'clients', 'postcode', "TEXT")
        self._ensure_column(cursor, 'clients', 'telephone', "TEXT")
        self._ensure_column(cursor, 'clients', 'email', "TEXT")
        self._ensure_column(cursor, 'clients', 'contact_person', "TEXT")
        self._ensure_column(cursor, 'clients', 'updated_by', "TEXT")
        self._ensure_column(cursor, 'clients', 'updated_date', "TIMESTAMP")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT UNIQUE,
                client_id INTEGER NOT NULL,
                transaction_date DATE NOT NULL,
                amount DECIMAL(15, 2) NOT NULL,
                transaction_type TEXT NOT NULL CHECK(transaction_type IN ('Receipt', 'Payment', 'Transfer')),
                reference TEXT NOT NULL,
                source TEXT NOT NULL CHECK(source IN ('Cash', 'Cheque', 'Bank Transfer', 'Card')),
                description TEXT,
                linked_cashbook_id INTEGER,
                is_reconciled INTEGER DEFAULT 0,
                reconciled_date DATE,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT DEFAULT 'System',
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE RESTRICT,
                CHECK(reference != '' AND transaction_date IS NOT NULL)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cashbook_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT UNIQUE,
                transaction_date DATE NOT NULL,
                amount DECIMAL(15, 2) NOT NULL,
                transaction_type TEXT NOT NULL CHECK(transaction_type IN ('Receipt', 'Payment')),
                reference TEXT NOT NULL,
                source TEXT NOT NULL CHECK(source IN ('Cash', 'Cheque', 'Bank Transfer', 'Card')),
                description TEXT,
                status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Cleared', 'Declined')),
                linked_ledger_id INTEGER,
                cleared_date DATE,
                declined_by TEXT,
                declined_at TIMESTAMP,
                decline_reason TEXT,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT DEFAULT 'System',
                FOREIGN KEY (linked_ledger_id) REFERENCES ledger_transactions(id),
                CHECK(reference != '' AND transaction_date IS NOT NULL)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','staff')),
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cheque_status_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cashbook_id INTEGER NOT NULL,
                previous_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                changed_by TEXT NOT NULL,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (cashbook_id) REFERENCES cashbook_transactions(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS month_locks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lock_month INTEGER NOT NULL,
                lock_year INTEGER NOT NULL,
                locked INTEGER NOT NULL DEFAULT 1,
                locked_by TEXT NOT NULL,
                locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                unlocked_by TEXT,
                unlocked_at TIMESTAMP,
                UNIQUE(lock_month, lock_year)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_trail (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                old_values TEXT,
                new_values TEXT,
                user TEXT DEFAULT 'System',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reason TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                module TEXT NOT NULL,
                record_id TEXT,
                details TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reconciliation_bank_session (
                user_id INTEGER PRIMARY KEY,
                bank_entries_json TEXT,
                matching_results_json TEXT,
                date_from TEXT,
                date_to TEXT,
                manual_review INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._ensure_column(cursor, 'reconciliation_bank_session', 'manual_review', 'INTEGER DEFAULT 0')

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reconciliations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reconciliation_date DATE NOT NULL,
                reconciliation_month INTEGER NOT NULL,
                reconciliation_year INTEGER NOT NULL,
                ledger_total DECIMAL(15, 2) NOT NULL,
                cashbook_total DECIMAL(15, 2) NOT NULL,
                bank_balance DECIMAL(15, 2) NOT NULL,
                variance DECIMAL(15, 2),
                notes TEXT,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT,
                updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            INSERT OR IGNORE INTO system_config (key, value, description)
            VALUES ('cheque_clearance_days', '5', 'Number of working days for cheque clearance')
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO system_config (key, value, description)
            VALUES ('client_code_seq', '1', 'Next client code sequence number for CLC-XXXX-XXXX format')
        """)
        # Migration: if existing CLC clients exist, set seq to max+1
        cursor.execute("SELECT client_code FROM clients WHERE client_code LIKE 'CLC-%'")
        for row in cursor.fetchall():
            try:
                parts = row[0].split('-')
                if len(parts) == 3 and len(parts[1]) == 4 and len(parts[2]) == 4:
                    p1, p2 = int(parts[1]), int(parts[2])
                    n = (p1 - 1) * 9999 + p2
                    cursor.execute("SELECT value FROM system_config WHERE key = 'client_code_seq'")
                    r = cursor.fetchone()
                    current = int(r[0]) if r else 1
                    if n >= current:
                        cursor.execute("UPDATE system_config SET value = ?, updated_date = CURRENT_TIMESTAMP WHERE key = 'client_code_seq'",
                                      (str(n + 1),))
            except (ValueError, IndexError):
                pass
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS office_cashbook (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT UNIQUE,
                transaction_date DATE NOT NULL,
                amount DECIMAL(15, 2) NOT NULL,
                transaction_type TEXT NOT NULL CHECK(transaction_type IN ('Receipt', 'Payment')),
                reference TEXT NOT NULL,
                source TEXT NOT NULL CHECK(source IN ('Cash', 'Cheque', 'Bank Transfer', 'Card')),
                description TEXT,
                status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Cleared', 'Declined')),
                cleared_date DATE,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT DEFAULT 'System',
                CHECK(reference != '' AND transaction_date IS NOT NULL)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS office_fee_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT UNIQUE,
                transaction_date DATE NOT NULL,
                amount DECIMAL(15, 2) NOT NULL,
                client_id INTEGER NOT NULL,
                ledger_transaction_id INTEGER NOT NULL,
                reference TEXT NOT NULL,
                description TEXT,
                created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT DEFAULT 'System',
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE RESTRICT,
                FOREIGN KEY (ledger_transaction_id) REFERENCES ledger_transactions(id) ON DELETE RESTRICT
            )
        """)
        
        self._ensure_column(cursor, 'office_fee_transfers', 'created_by', "TEXT DEFAULT 'System'")
        self._ensure_column(cursor, 'ledger_transactions', 'transaction_id', "TEXT")
        self._ensure_column(cursor, 'ledger_transactions', 'reversal_status', "TEXT DEFAULT 'ACTIVE'")
        self._ensure_column(cursor, 'ledger_transactions', 'reversed_at', "TIMESTAMP")
        self._ensure_column(cursor, 'ledger_transactions', 'reversed_by', "TEXT")
        self._ensure_column(cursor, 'ledger_transactions', 'reversal_of', "INTEGER")
        self._ensure_column(cursor, 'ledger_transactions', 'parent_transaction_id', "INTEGER")
        self._ensure_column(cursor, 'ledger_transactions', 'reversal_of_transaction_id', "INTEGER")
        self._ensure_column(cursor, 'ledger_transactions', 'reversal_reason', "TEXT")
        self._ensure_column(cursor, 'ledger_transactions', 'reversal_depth', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'ledger_transactions', 'is_deleted', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'ledger_transactions', 'deleted_at', "TIMESTAMP")
        self._ensure_column(cursor, 'ledger_transactions', 'deleted_by', "TEXT")
        self._ensure_column(cursor, 'ledger_transactions', 'deleted_reason', "TEXT")
        self._ensure_column(cursor, 'cashbook_transactions', 'transaction_id', "TEXT")
        self._ensure_column(cursor, 'cashbook_transactions', 'reversal_status', "TEXT DEFAULT 'ACTIVE'")
        self._ensure_column(cursor, 'cashbook_transactions', 'reversed_at', "TIMESTAMP")
        self._ensure_column(cursor, 'cashbook_transactions', 'reversed_by', "TEXT")
        self._ensure_column(cursor, 'cashbook_transactions', 'reversal_of', "INTEGER")
        self._ensure_column(cursor, 'cashbook_transactions', 'parent_transaction_id', "INTEGER")
        self._ensure_column(cursor, 'cashbook_transactions', 'reversal_of_transaction_id', "INTEGER")
        self._ensure_column(cursor, 'cashbook_transactions', 'reversal_depth', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'cashbook_transactions', 'declined_by', "TEXT")
        self._ensure_column(cursor, 'cashbook_transactions', 'declined_at', "TIMESTAMP")
        self._ensure_column(cursor, 'cashbook_transactions', 'decline_reason', "TEXT")
        self._ensure_column(cursor, 'cashbook_transactions', 'is_deleted', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'cashbook_transactions', 'deleted_at', "TIMESTAMP")
        self._ensure_column(cursor, 'cashbook_transactions', 'deleted_by', "TEXT")
        self._ensure_column(cursor, 'cashbook_transactions', 'deleted_reason', "TEXT")
        self._ensure_column(cursor, 'cheque_status_log', 'is_deleted', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'cheque_status_log', 'deleted_at', "TIMESTAMP")
        self._ensure_column(cursor, 'cheque_status_log', 'deleted_by', "TEXT")
        self._ensure_column(cursor, 'cheque_status_log', 'deleted_reason', "TEXT")
        self._ensure_column(cursor, 'office_cashbook', 'declined_by', "TEXT")
        self._ensure_column(cursor, 'office_cashbook', 'declined_at', "TIMESTAMP")
        self._ensure_column(cursor, 'office_cashbook', 'decline_reason', "TEXT")
        self._ensure_column(cursor, 'office_cashbook', 'is_deleted', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'office_cashbook', 'deleted_at', "TIMESTAMP")
        self._ensure_column(cursor, 'office_cashbook', 'deleted_by', "TEXT")
        self._ensure_column(cursor, 'office_cashbook', 'deleted_reason', "TEXT")
        self._ensure_column(cursor, 'office_fee_transfers', 'transaction_id', "TEXT")
        self._ensure_column(cursor, 'office_fee_transfers', 'is_deleted', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'office_fee_transfers', 'deleted_at', "TIMESTAMP")
        self._ensure_column(cursor, 'office_fee_transfers', 'deleted_by', "TEXT")
        self._ensure_column(cursor, 'office_fee_transfers', 'deleted_reason', "TEXT")
        self._ensure_column(cursor, 'reconciliations', 'is_deleted', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'reconciliations', 'deleted_at', "TIMESTAMP")
        self._ensure_column(cursor, 'reconciliations', 'deleted_by', "TEXT")
        self._ensure_column(cursor, 'reconciliations', 'deleted_reason', "TEXT")
        self._ensure_column(cursor, 'reconciliations', 'version', 'INTEGER DEFAULT 1')
        self._ensure_column(cursor, 'reconciliations', 'locked', 'INTEGER DEFAULT 0')
        self._ensure_column(cursor, 'reconciliations', 'is_current', 'INTEGER DEFAULT 0')
        self._ensure_column(cursor, 'reconciliations', 'locked_by_user', 'TEXT')
        self._ensure_column(cursor, 'reconciliations', 'locked_timestamp', 'TIMESTAMP')
        self._ensure_column(cursor, 'reconciliations', 'reconciled_by', 'TEXT')
        self._ensure_column(cursor, 'audit_trail', 'is_deleted', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'audit_trail', 'deleted_at', "TIMESTAMP")
        self._ensure_column(cursor, 'audit_trail', 'deleted_by', "TEXT")
        self._ensure_column(cursor, 'audit_trail', 'deleted_reason', "TEXT")
        self._ensure_column(cursor, 'audit_log', 'is_deleted', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'audit_log', 'deleted_at', "TIMESTAMP")
        self._ensure_column(cursor, 'audit_log', 'deleted_by', "TEXT")
        self._ensure_column(cursor, 'audit_log', 'deleted_reason', "TEXT")
        self._ensure_column(cursor, 'users', 'admin_recovery_key_hash', "TEXT")
        self._ensure_column(cursor, 'users', 'admin_recovery_attempts', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'users', 'admin_recovery_last_attempt', "TEXT")
        self._ensure_column(cursor, 'users', 'admin_recovery_key_used', "INTEGER DEFAULT 0")
        self._ensure_column(cursor, 'users', 'admin_recovery_key_created_at', "TEXT")
        self._ensure_column(cursor, 'users', 'name', "TEXT")
        self._ensure_column(cursor, 'users', 'temporary_password', "INTEGER DEFAULT 0")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                expiry_time TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        cursor.execute("INSERT OR IGNORE INTO system_config (key, value, description) VALUES ('txn_id_year', '0', 'Last year for TXN ID sequence')")
        cursor.execute("INSERT OR IGNORE INTO system_config (key, value, description) VALUES ('txn_id_seq', '0', 'Transaction ID sequence for current year')")
        self._migrate_transaction_ids(cursor)
        cursor.execute("""
            UPDATE ledger_transactions
            SET parent_transaction_id = COALESCE(parent_transaction_id, reversal_of),
                reversal_of_transaction_id = COALESCE(reversal_of_transaction_id, reversal_of)
            WHERE reversal_of IS NOT NULL
              AND (parent_transaction_id IS NULL OR reversal_of_transaction_id IS NULL)
        """)
        cursor.execute("""
            UPDATE cashbook_transactions
            SET parent_transaction_id = COALESCE(parent_transaction_id, reversal_of),
                reversal_of_transaction_id = COALESCE(reversal_of_transaction_id, reversal_of)
            WHERE reversal_of IS NOT NULL
              AND (parent_transaction_id IS NULL OR reversal_of_transaction_id IS NULL)
        """)
        self._ensure_column(cursor, 'users', 'portal_role', 'TEXT')
        self._ensure_column(cursor, 'users', 'is_system', 'INTEGER DEFAULT 0')
        self._backfill_ledger_reversal_depths(cursor)
        self._sync_cashbook_reversal_depth_from_ledger(cursor)
        self._migrate_reconciliation_versioning(cursor)
        conn.commit()
        if not self.skip_user_seed:
            self._seed_default_users(cursor)
        conn.commit()
        conn.close()
    
    def _ensure_column(self, cursor, table: str, column: str, definition: str):
        """Add column to an existing SQLite table if it is missing (simple migration helper)."""
        cursor.execute(f"PRAGMA table_info({table})")
        existing = [row[1] for row in cursor.fetchall()]
        if column not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _migrate_reconciliation_versioning(self, cursor) -> None:
        """Ensure versioning columns, one-current-per-month index, and normalize existing rows."""
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_recon_one_current_per_month
            ON reconciliations(reconciliation_month, reconciliation_year)
            WHERE is_current = 1 AND COALESCE(is_deleted, 0) = 0
        """)
        cursor.execute("""
            UPDATE reconciliations SET version = COALESCE(version, 1)
            WHERE COALESCE(is_deleted, 0) = 0
        """)
        cursor.execute("""
            SELECT reconciliation_month, reconciliation_year
            FROM reconciliations
            WHERE COALESCE(is_deleted, 0) = 0
            GROUP BY reconciliation_month, reconciliation_year
        """)
        for month, year in cursor.fetchall():
            cursor.execute("""
                SELECT id, COALESCE(version, 1) AS version
                FROM reconciliations
                WHERE reconciliation_month = ? AND reconciliation_year = ?
                  AND COALESCE(is_deleted, 0) = 0
                ORDER BY version DESC, id DESC
            """, (month, year))
            rows = cursor.fetchall()
            if not rows:
                continue
            keep_id, keep_ver = rows[0][0], rows[0][1]
            cursor.execute("""
                UPDATE reconciliations SET is_current = CASE WHEN id = ? THEN 1 ELSE 0 END
                WHERE reconciliation_month = ? AND reconciliation_year = ?
                  AND COALESCE(is_deleted, 0) = 0
            """, (keep_id, month, year))
            for rec_id, ver in rows[1:]:
                if ver < keep_ver:
                    cursor.execute("""
                        UPDATE reconciliations SET is_current = 0, locked = 1 WHERE id = ?
                    """, (rec_id,))
                else:
                    cursor.execute("""
                        UPDATE reconciliations
                        SET is_deleted = 1, is_current = 0, locked = 1,
                            deleted_at = CURRENT_TIMESTAMP, deleted_by = 'System',
                            deleted_reason = 'Duplicate reconciliation removed during versioning migration'
                        WHERE id = ?
                    """, (rec_id,))
        cursor.execute("""
            UPDATE reconciliations SET locked = 1
            WHERE is_current = 1 AND COALESCE(is_deleted, 0) = 0
              AND EXISTS (
                SELECT 1 FROM month_locks ml
                WHERE ml.lock_month = reconciliations.reconciliation_month
                  AND ml.lock_year = reconciliations.reconciliation_year
                  AND ml.locked = 1
              )
        """)
        cursor.execute("""
            SELECT reconciliation_month, reconciliation_year, version
            FROM reconciliations
            WHERE COALESCE(is_deleted, 0) = 0
            GROUP BY reconciliation_month, reconciliation_year, version
            HAVING COUNT(*) > 1
        """)
        for month, year, ver in cursor.fetchall():
            cursor.execute("""
                SELECT id FROM reconciliations
                WHERE reconciliation_month = ? AND reconciliation_year = ?
                  AND COALESCE(version, 1) = ? AND COALESCE(is_deleted, 0) = 0
                ORDER BY id
            """, (month, year, ver))
            dup_ids = [r[0] for r in cursor.fetchall()]
            for rec_id in dup_ids[1:]:
                cursor.execute("""
                    UPDATE reconciliations
                    SET is_deleted = 1, is_current = 0,
                        deleted_at = CURRENT_TIMESTAMP, deleted_by = 'System',
                        deleted_reason = 'Duplicate version row removed during versioning migration'
                    WHERE id = ?
                """, (rec_id,))

    def _backfill_ledger_reversal_depths(self, cursor) -> None:
        """Set ledger reversal_depth from chain (parent.depth + 1) until stable."""
        for _ in range(512):
            cursor.execute("""
                UPDATE ledger_transactions AS lt
                SET reversal_depth = (
                    SELECT COALESCE(p.reversal_depth, 0) + 1
                    FROM ledger_transactions p WHERE p.id = lt.reversal_of
                )
                WHERE lt.reversal_of IS NOT NULL
                  AND (
                    lt.reversal_depth IS NULL
                    OR lt.reversal_depth != (
                        SELECT COALESCE(p.reversal_depth, 0) + 1
                        FROM ledger_transactions p WHERE p.id = lt.reversal_of
                    )
                  )
            """)
            if cursor.rowcount == 0:
                break

    def _sync_cashbook_reversal_depth_from_ledger(self, cursor) -> None:
        """Mirror ledger reversal_depth onto linked cashbook rows."""
        cursor.execute("""
            UPDATE cashbook_transactions
            SET reversal_depth = COALESCE((
                SELECT lt.reversal_depth FROM ledger_transactions lt
                WHERE lt.id = cashbook_transactions.linked_ledger_id
            ), 0)
            WHERE linked_ledger_id IS NOT NULL
        """)

    def initialize_security_columns(self):
        """
        Ensure users table has all security/recovery columns. Run at app startup.
        Safely adds missing columns without removing any data.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA table_info(users)")
            existing = [row[1] for row in cursor.fetchall()]
            columns_to_add = [
                ('admin_recovery_key_hash', 'TEXT'),
                ('admin_recovery_attempts', 'INTEGER DEFAULT 0'),
                ('admin_recovery_last_attempt', 'TEXT'),
                ('admin_recovery_key_used', 'INTEGER DEFAULT 0'),
                ('admin_recovery_key_created_at', 'TEXT'),
                ('failed_login_attempts', 'INTEGER DEFAULT 0'),
                ('last_failed_login', 'TEXT'),
                ('lockout_until', 'TEXT'),
                ('lockout_level', 'INTEGER DEFAULT 0'),
                ('admin_recovery_confirm_attempts', 'INTEGER DEFAULT 0'),
                ('admin_recovery_confirm_lockout_until', 'TEXT'),
                ('admin_recovery_confirm_lockout_level', 'INTEGER DEFAULT 0'),
                ('name', 'TEXT'),
                ('temporary_password', 'INTEGER DEFAULT 0'),
                ('portal_user_id', 'TEXT'),
                ('email', 'TEXT'),
                ('firm_id', 'TEXT'),
                ('portal_role', 'TEXT'),
                ('is_system', 'INTEGER DEFAULT 0'),
                ('full_name', 'TEXT'),
            ]
            for col_name, col_def in columns_to_add:
                if col_name not in existing:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
            cursor.execute("""
                UPDATE users SET is_system = 1
                WHERE username IN ('admin', 'staff')
                  AND (portal_user_id IS NULL OR portal_user_id = '')
                  AND COALESCE(is_system, 0) = 0
            """)
            conn.commit()
        finally:
            conn.close()

    def _seed_default_users(self, cursor):
        cursor.execute("SELECT value FROM system_config WHERE key = 'provisioned_tenant'")
        if cursor.fetchone() or self.skip_user_seed:
            return
        defaults = [
            ('admin', 'admin', 'admin'),
            ('staff', 'staff', 'staff'),
        ]
        is_system = 1 if self.is_template else 0
        for username, password, role in defaults:
            cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO users (username, password_hash, role, is_system) VALUES (?, ?, ?, ?)",
                    (username, generate_password_hash(password), role, is_system)
                )
        # One-time migration: ensure default test passwords are admin/staff
        cursor.execute("SELECT value FROM system_config WHERE key = 'default_passwords_updated'")
        if not cursor.fetchone():
            for username, password, role in defaults:
                cursor.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                              (generate_password_hash(password), username))
            cursor.execute("INSERT OR IGNORE INTO system_config (key, value, description) VALUES ('default_passwords_updated', '1', 'Default user passwords set to admin/staff')")
        if not self.is_template:
            cursor.execute("""
                UPDATE users SET is_system = 1
                WHERE username IN ('admin', 'staff')
            """)
    
    def reset_database(self, confirm: bool = False):
        """
        Wipe ALL data and reset the system to a fresh-install state.

        Preserves schema, re-seeds default users (admin/staff), and resets
        all sequences to their initial values.

        Args:
            confirm: Must be explicitly True to execute. Prevents accidental calls.

        Raises:
            RuntimeError: If confirm is not True or if SOLICITOR_NO_RESET env var is set.
        """
        if os.environ.get('SOLICITOR_NO_RESET', ''):
            raise RuntimeError('Database reset is blocked by SOLICITOR_NO_RESET environment variable.')
        if confirm is not True:
            raise RuntimeError(
                'reset_database() requires confirm=True. '
                'This will permanently delete ALL data.'
            )

        data_tables = [
            'cheque_status_log',
            'office_fee_transfers',
            'reconciliation_bank_session',
            'reconciliations',
            'month_locks',
            'audit_trail',
            'audit_log',
            'reset_tokens',
            'office_cashbook',
            'cashbook_transactions',
            'ledger_transactions',
            'clients',
        ]

        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = OFF")
            for table in data_tables:
                cursor.execute(f"DELETE FROM {table}")
            cursor.execute("DELETE FROM users")
            cursor.execute("UPDATE system_config SET value = '1' WHERE key = 'client_code_seq'")
            cursor.execute("UPDATE system_config SET value = '0' WHERE key = 'txn_id_seq'")
            cursor.execute("UPDATE system_config SET value = '0' WHERE key = 'txn_id_year'")
            cursor.execute("DELETE FROM system_config WHERE key = 'default_passwords_updated'")
            cursor.execute("PRAGMA foreign_keys = ON")
            conn.commit()
            self._seed_default_users(cursor)
            conn.commit()
        finally:
            conn.close()

        # Reset SQLite auto-increment counters
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sqlite_sequence")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    def full_system_reset(
        self,
        confirm: bool,
        delete_clients: bool,
        performed_by: str,
    ) -> Dict[str, int]:
        """
        Wipe all client-money and office transactional data, reconciliations, month locks, and audit logs.
        Preserves user accounts and system_config (except txn / client-code sequences).

        Optional: delete all client/matter rows (delete_clients=True) or keep them for fresh posting.

        Safety:
        - confirm must be True
        - ALLOW_FULL_SYSTEM_RESET must be exactly 'true' (case-insensitive)
        - Blocked if SOLICITOR_NO_RESET is set (same as reset_database)

        Raises:
            RuntimeError: If guards fail.
            ValueError: If validation fails after deletes (transaction rolled back).
        """
        if os.environ.get('SOLICITOR_NO_RESET', ''):
            raise RuntimeError('Database reset is blocked by SOLICITOR_NO_RESET environment variable.')
        if confirm is not True:
            raise RuntimeError('full_system_reset requires confirm=True.')
        if os.environ.get('ALLOW_FULL_SYSTEM_RESET', '').strip().lower() != 'true':
            raise RuntimeError(
                'Full system reset is disabled. Set environment variable ALLOW_FULL_SYSTEM_RESET=true to enable.'
            )

        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('BEGIN IMMEDIATE')
                cursor.execute('PRAGMA foreign_keys = OFF')

                tables_order = [
                    'cheque_status_log',
                    'office_fee_transfers',
                    'reconciliation_bank_session',
                    'reconciliations',
                    'month_locks',
                    'audit_trail',
                    'audit_log',
                    'reset_tokens',
                    'office_cashbook',
                    'cashbook_transactions',
                    'ledger_transactions',
                ]
                counts: Dict[str, int] = {}
                for t in tables_order:
                    cursor.execute(f'DELETE FROM {t}')
                    counts[f'{t}_rows_deleted'] = cursor.rowcount

                if delete_clients:
                    cursor.execute('DELETE FROM clients')
                    counts['clients_rows_deleted'] = cursor.rowcount
                else:
                    counts['clients_rows_deleted'] = 0

                cursor.execute("UPDATE system_config SET value = '0' WHERE key = 'txn_id_seq'")
                cursor.execute("UPDATE system_config SET value = '0' WHERE key = 'txn_id_year'")
                if delete_clients:
                    cursor.execute("UPDATE system_config SET value = '1' WHERE key = 'client_code_seq'")
                cursor.execute("DELETE FROM system_config WHERE key = 'default_passwords_updated'")

                cursor.execute('PRAGMA foreign_keys = ON')

                def _cnt(table: str) -> int:
                    cursor.execute(f'SELECT COUNT(*) FROM {table}')
                    return int(cursor.fetchone()[0])

                checks = [
                    ('ledger_transactions', 0),
                    ('cashbook_transactions', 0),
                    ('office_cashbook', 0),
                    ('office_fee_transfers', 0),
                    ('reconciliations', 0),
                    ('audit_trail', 0),
                    ('audit_log', 0),
                    ('cheque_status_log', 0),
                    ('month_locks', 0),
                    ('reset_tokens', 0),
                ]
                for tbl, expected in checks:
                    n = _cnt(tbl)
                    if n != expected:
                        conn.rollback()
                        raise ValueError(
                            f'Reset rolled back: table {tbl} has {n} rows, expected {expected}.'
                        )

                led = self._cursor_sum_all_clients_ledger(cursor, None)
                cb = self._cursor_sum_all_client_cashbook(cursor, None)
                if led != Decimal('0') or cb != Decimal('0'):
                    conn.rollback()
                    raise ValueError(
                        f'Reset rolled back: client ledger £{led} or client cashbook £{cb} not zero.'
                    )

                seq_tables = [
                    'cheque_status_log',
                    'office_fee_transfers',
                    'reconciliation_bank_session',
                    'reconciliations',
                    'month_locks',
                    'audit_trail',
                    'audit_log',
                    'reset_tokens',
                    'office_cashbook',
                    'cashbook_transactions',
                    'ledger_transactions',
                ]
                if delete_clients:
                    seq_tables.append('clients')
                try:
                    for name in seq_tables:
                        cursor.execute('DELETE FROM sqlite_sequence WHERE name = ?', (name,))
                except sqlite3.OperationalError:
                    pass

                conn.commit()
                logger.info(
                    'full_system_reset by %s delete_clients=%s counts=%s',
                    performed_by, delete_clients, counts,
                )
                return counts
            except ValueError:
                conn.rollback()
                raise
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('full_system_reset', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f'Database error: {e}') from e
            finally:
                conn.close()
        raise ValueError(f'Database busy after {DB_WRITE_RETRIES} retries: {last_err}')

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username.lower(),))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_user_by_login_identifier(self, identifier: str) -> Optional[Dict]:
        """Return user matching username or email (case-insensitive)."""
        ident = (identifier or "").strip().lower()
        if not ident:
            return None
        self.initialize_security_columns()
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT * FROM users
                WHERE lower(username) = ? OR lower(COALESCE(email, '')) = ?
                LIMIT 1
                """,
                (ident, ident),
            )
            row = cursor.fetchone()
        except sqlite3.OperationalError:
            cursor.execute(
                "SELECT * FROM users WHERE lower(username) = ? LIMIT 1",
                (ident,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def get_admin_by_login_identifier(self, identifier: str) -> Optional[Dict]:
        """Return admin user matching username or email (case-insensitive)."""
        ident = (identifier or "").strip().lower()
        if not ident:
            return None
        self.initialize_security_columns()
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT * FROM users
                WHERE role = 'admin'
                  AND (lower(username) = ? OR lower(COALESCE(email, '')) = ?)
                LIMIT 1
                """,
                (ident, ident),
            )
            row = cursor.fetchone()
        except sqlite3.OperationalError:
            cursor.execute(
                """
                SELECT * FROM users
                WHERE role = 'admin' AND lower(username) = ?
                LIMIT 1
                """,
                (ident,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_admin_user_by_username(self, username: str) -> Optional[Dict]:
        """Return admin user by username for recovery flow."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE lower(username) = lower(?) AND role = 'admin'", (username,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def set_admin_recovery_key_hash(self, user_id: int, key_hash: Optional[str]):
        """Set or clear recovery key hash. On set: clears attempts, resets used flag. On clear: sets used=1."""
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        if key_hash:
            cursor.execute("""
                UPDATE users SET admin_recovery_key_hash = ?, admin_recovery_attempts = 0,
                    admin_recovery_last_attempt = NULL, admin_recovery_key_used = 0,
                    admin_recovery_key_created_at = ?
                WHERE user_id = ? AND role = 'admin'
            """, (key_hash, now, user_id))
        else:
            cursor.execute("""
                UPDATE users SET admin_recovery_key_hash = NULL, admin_recovery_key_used = 1
                WHERE user_id = ? AND role = 'admin'
            """, (user_id,))
        conn.commit()
        conn.close()

    def is_admin_recovery_key_used(self, user_id: int) -> bool:
        """Check if the admin's recovery key has been used."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT admin_recovery_key_used FROM users WHERE user_id = ? AND role = 'admin'",
            (user_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return bool(row and row['admin_recovery_key_used'])

    def mark_admin_recovery_key_used(self, user_id: int):
        """Mark the recovery key as used after successful password reset."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET admin_recovery_key_used = 1 WHERE user_id = ? AND role = 'admin'
        """, (user_id,))
        conn.commit()
        conn.close()

    def update_admin_recovery_attempt(self, user_id: int, attempts: int, last_attempt_iso: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET admin_recovery_attempts = ?, admin_recovery_last_attempt = ? WHERE user_id = ?
        """, (attempts, last_attempt_iso, user_id))
        conn.commit()
        conn.close()

    def update_user_password(self, user_id: int, password_hash: str):
        """Update user password by user_id (used for recovery reset)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET password_hash = ? WHERE user_id = ?", (password_hash, user_id))
        conn.commit()
        conn.close()

    LOCKOUT_MINUTES = (5, 60, 24 * 60)

    def _lockout_duration_minutes(self, level: int) -> int:
        idx = min(level, len(self.LOCKOUT_MINUTES) - 1)
        return self.LOCKOUT_MINUTES[idx]

    def is_login_locked(self, username: str) -> tuple:
        """Return (is_locked, lockout_until_str, remaining_str)."""
        user = self.get_user_by_username(username)
        if not user:
            return (False, None, None)
        until = user.get('lockout_until')
        if not until:
            return (False, None, None)
        try:
            s = until.replace('Z', '')[:19]
            dt = datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
        except Exception:
            return (False, None, None)
        now = datetime.utcnow()
        if now >= dt:
            return (False, None, None)
        delta = dt - now
        mins = int(delta.total_seconds() / 60) + 1
        if mins >= 1440:
            days = mins // 1440
            remain = f"{days} day{'s' if days > 1 else ''}"
        elif mins >= 60:
            hrs = mins // 60
            remain = f"{hrs} hour{'s' if hrs > 1 else ''}"
        else:
            remain = f"{mins} minute{'s' if mins > 1 else ''}"
        return (True, until, remain)

    def record_failed_login(self, username: str) -> tuple:
        """Return (message, is_locked, lockout_until_str, remaining_str)."""
        user = self.get_user_by_username(username)
        if not user:
            return ('Invalid username or password.', False, None, None)
        conn = self.get_connection()
        cursor = conn.cursor()
        attempts = (user.get('failed_login_attempts') or 0) + 1
        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        level = user.get('lockout_level') or 0
        until_str = None
        remaining = None
        if attempts >= 5:
            mins = self._lockout_duration_minutes(level)
            from datetime import timedelta
            unlock = datetime.utcnow() + timedelta(minutes=mins)
            until_str = unlock.strftime('%Y-%m-%dT%H:%M:%SZ')
            if mins >= 1440:
                remaining = f"{mins // 1440} day{'s' if mins >= 2880 else ''}"
            elif mins >= 60:
                remaining = f"{mins // 60} hour{'s' if mins >= 120 else ''}"
            else:
                remaining = f"{mins} minute{'s' if mins > 1 else ''}"
            cursor.execute("""
                UPDATE users SET failed_login_attempts = 0, last_failed_login = ?,
                    lockout_until = ?, lockout_level = ?
                WHERE user_id = ?
            """, (now, until_str, level + 1, user['user_id']))
            conn.commit()
            conn.close()
            return (f"Account temporarily locked.\nPlease try again in {remaining}.", True, until_str, remaining)
        if attempts == 4:
            msg = '2 attempts remaining before temporary lockout.'
        else:
            msg = 'Invalid username or password.'
        cursor.execute("""
            UPDATE users SET failed_login_attempts = ?, last_failed_login = ?
            WHERE user_id = ?
        """, (attempts, now, user['user_id']))
        conn.commit()
        conn.close()
        return (msg, False, None, None)

    def reset_login_attempts(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET failed_login_attempts = 0, last_failed_login = NULL,
                lockout_until = NULL, lockout_level = 0
            WHERE user_id = ?
        """, (user_id,))
        conn.commit()
        conn.close()

    def is_recovery_confirm_locked(self, user_id: int) -> tuple:
        """Return (is_locked, lockout_until_str, remaining_str)."""
        user = self.get_user_by_id(user_id)
        if not user:
            return (False, None, None)
        until = user.get('admin_recovery_confirm_lockout_until')
        if not until:
            return (False, None, None)
        try:
            s = until.replace('Z', '')[:19]
            dt = datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
        except Exception:
            return (False, None, None)
        now = datetime.utcnow()
        if now >= dt:
            return (False, None, None)
        delta = dt - now
        mins = int(delta.total_seconds() / 60) + 1
        if mins >= 1440:
            days = mins // 1440
            remain = f"{days} day{'s' if days > 1 else ''}"
        elif mins >= 60:
            hrs = mins // 60
            remain = f"{hrs} hour{'s' if hrs > 1 else ''}"
        else:
            remain = f"{mins} minute{'s' if mins > 1 else ''}"
        return (True, until, remain)

    def record_failed_recovery_confirm(self, user_id: int) -> tuple:
        """Return (message, is_locked, remaining_str)."""
        user = self.get_user_by_id(user_id)
        if not user:
            return ('Invalid password.', False, None)
        conn = self.get_connection()
        cursor = conn.cursor()
        attempts = (user.get('admin_recovery_confirm_attempts') or 0) + 1
        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        level = user.get('admin_recovery_confirm_lockout_level') or 0
        until_str = None
        remaining = None
        if attempts >= 5:
            mins = self._lockout_duration_minutes(level)
            from datetime import timedelta
            unlock = datetime.utcnow() + timedelta(minutes=mins)
            until_str = unlock.strftime('%Y-%m-%dT%H:%M:%SZ')
            if mins >= 1440:
                remaining = f"{mins // 1440} day{'s' if mins >= 2880 else ''}"
            elif mins >= 60:
                remaining = f"{mins // 60} hour{'s' if mins >= 120 else ''}"
            else:
                remaining = f"{mins} minute{'s' if mins > 1 else ''}"
            cursor.execute("""
                UPDATE users SET admin_recovery_confirm_attempts = 0,
                    admin_recovery_confirm_lockout_until = ?,
                    admin_recovery_confirm_lockout_level = ?
                WHERE user_id = ? AND role = 'admin'
            """, (until_str, level + 1, user_id))
            conn.commit()
            conn.close()
            return (f"Account temporarily locked.\nPlease try again in {remaining}.", True, remaining)
        if attempts == 4:
            msg = '2 attempts remaining before temporary lockout.'
        else:
            msg = 'Invalid password. Recovery key was not generated.'
        cursor.execute("""
            UPDATE users SET admin_recovery_confirm_attempts = ?
            WHERE user_id = ? AND role = 'admin'
        """, (attempts, user_id))
        conn.commit()
        conn.close()
        return (msg, False, None)

    def reset_recovery_confirm_attempts(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users SET admin_recovery_confirm_attempts = 0,
                admin_recovery_confirm_lockout_until = NULL,
                admin_recovery_confirm_lockout_level = 0
            WHERE user_id = ? AND role = 'admin'
        """, (user_id,))
        conn.commit()
        conn.close()

    def verify_user_credentials(self, username: str, password: str) -> Optional[Dict]:
        from lib.password_verification import verify_password

        user = self.get_user_by_username(username)
        if not user or not user.get('active'):
            return None
        if verify_password(user['password_hash'], password):
            return user
        return None

    def get_active_users(self) -> List[Dict]:
        """Billable active users for reports and Created By filters."""
        return self.get_billable_active_users()

    def get_billable_active_users(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username, role
            FROM users
            WHERE active = 1 AND COALESCE(is_system, 0) = 0
            ORDER BY username
        """)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def count_billable_active_users(self) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM users
            WHERE active = 1 AND COALESCE(is_system, 0) = 0
        """)
        total = int(cursor.fetchone()[0])
        conn.close()
        return total

    def get_all_users(self) -> List[Dict]:
        """Return billable users for user management (excludes system accounts)."""
        return self.get_billable_users_for_management()

    def get_billable_users_for_management(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username, name, role, active
            FROM users
            WHERE COALESCE(is_system, 0) = 0
            ORDER BY username
        """)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def get_all_users_unfiltered(self) -> List[Dict]:
        """Return all users including system accounts (internal use only)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, name, role, active, is_system FROM users ORDER BY username")
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def create_user(self, username: str, password_hash: str, role: str, name: str = None, temporary: bool = True) -> int:
        """Create user. Returns user_id."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (username, password_hash, role, name, temporary_password, is_system)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (username.lower().strip(), password_hash, role, (name or '').strip() or None, 1 if temporary else 0))
        uid = cursor.lastrowid
        conn.commit()
        conn.close()
        return uid

    def update_user_role(self, user_id: int, new_role: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET role = ? WHERE user_id = ?", (new_role, user_id))
        conn.commit()
        conn.close()

    def set_user_active(self, user_id: int, active: bool):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET active = ? WHERE user_id = ?", (1 if active else 0, user_id))
        conn.commit()
        conn.close()

    def create_reset_token(self, user_id: int) -> tuple:
        """Create reset token. Returns (token, expiry_iso). Expires in 30 minutes."""
        conn = self.get_connection()
        cursor = conn.cursor()
        token = secrets.token_urlsafe(32)
        expiry = datetime.utcnow() + __import__('datetime').timedelta(minutes=30)
        expiry_str = expiry.strftime('%Y-%m-%dT%H:%M:%SZ')
        cursor.execute("""
            INSERT INTO reset_tokens (user_id, token, expiry_time) VALUES (?, ?, ?)
        """, (user_id, token, expiry_str))
        conn.commit()
        conn.close()
        return (token, expiry_str)

    def get_reset_token_user(self, token: str) -> Optional[Dict]:
        """Validate token, return user if valid and not expired/used."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, used, expiry_time FROM reset_tokens WHERE token = ?",
            (token,)
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        if row['used']:
            return None
        try:
            s = str(row['expiry_time']).replace('Z', '')[:19]
            exp = datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
        except Exception:
            return None
        if datetime.utcnow() >= exp:
            return None
        return self.get_user_by_id(row['user_id'])

    def mark_reset_token_used(self, token: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE reset_tokens SET used = 1 WHERE token = ?", (token,))
        conn.commit()
        conn.close()

    def clear_temporary_password(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET temporary_password = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def has_temporary_password(self, user_id: int) -> bool:
        user = self.get_user_by_id(user_id)
        return bool(user and user.get('temporary_password'))

    def is_month_locked(self, month: int, year: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT locked FROM month_locks WHERE lock_month = ? AND lock_year = ?
        """, (month, year))
        row = cursor.fetchone()
        conn.close()
        return bool(row and row['locked'])

    def get_locked_months(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM month_locks ORDER BY lock_year DESC, lock_month DESC")
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def lock_month(self, month: int, year: int, locked_by: str):
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO month_locks (lock_month, lock_year, locked, locked_by, locked_at, unlocked_by, unlocked_at)
                    VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP, NULL, NULL)
                    ON CONFLICT(lock_month, lock_year) DO UPDATE SET
                        locked = 1,
                        locked_by = excluded.locked_by,
                        locked_at = CURRENT_TIMESTAMP,
                        unlocked_by = NULL,
                        unlocked_at = NULL
                """, (month, year, locked_by))
                conn.commit()
                return
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('lock_month', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def unlock_month(self, month: int, year: int, unlocked_by: str):
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE month_locks
                    SET locked = 0,
                        unlocked_by = ?,
                        unlocked_at = CURRENT_TIMESTAMP
                    WHERE lock_month = ? AND lock_year = ?
                """, (unlocked_by, month, year))
                if cursor.rowcount == 0:
                    raise ValueError("Month was not locked.")
                conn.commit()
                return
            except ValueError:
                conn.rollback()
                raise
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('unlock_month', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def get_month_lock_row(self, month: int, year: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM month_locks WHERE lock_month = ? AND lock_year = ?",
            (month, year),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_current_reconciliation(self, month: int, year: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM reconciliations
            WHERE reconciliation_month = ? AND reconciliation_year = ?
              AND is_current = 1 AND COALESCE(is_deleted, 0) = 0
        """, (month, year))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def lock_reconciliation_month(self, month: int, year: int, locked_by: str,
                                  reconciliation_date: str, ledger_total: Decimal,
                                  cashbook_total: Decimal, bank_balance: Decimal,
                                  notes: str = None) -> Dict:
        """
        Lock a reconciled month using live month-end figures.
        First lock snapshots the current version. After unlock, relock keeps the same
        version if figures are unchanged; creates the next version only when values differ.
        """
        from reconciliation_utils import compute_reconciliation_state, reconciliation_figures_changed

        state = compute_reconciliation_state(ledger_total, cashbook_total, bank_balance)
        if not state['can_complete']:
            raise ValueError(
                'Cannot lock reconciliation: Client Ledger, Cashbook, and bank balance must '
                'agree within £0.01 using live month-end figures.'
            )

        current = self.get_current_reconciliation(month, year)
        if not current:
            raise ValueError(
                'Record a reconciliation for this month before locking. '
                'Use New Reconciliation first.'
            )

        if self.is_month_locked(month, year) and current.get('locked'):
            raise ValueError(f'{year}-{month:02d} is already locked.')

        ledger_total = state['ledger_total']
        cashbook_total = state['cashbook_total']
        bank_balance = state['bank_balance']
        variance = state['variance']

        was_previously_locked = bool(current.get('locked_timestamp'))
        is_relock_after_unlock = was_previously_locked and not current.get('locked')
        figures_changed = (
            reconciliation_figures_changed(current, ledger_total, cashbook_total, bank_balance)
            if is_relock_after_unlock else True
        )

        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                if is_relock_after_unlock and figures_changed:
                    new_version = int(current.get('version') or 1) + 1
                    cursor.execute("""
                        UPDATE reconciliations
                        SET is_current = 0, locked = 1
                        WHERE id = ?
                    """, (current['id'],))
                    cursor.execute("""
                        INSERT INTO reconciliations
                        (reconciliation_date, reconciliation_month, reconciliation_year,
                         ledger_total, cashbook_total, bank_balance, variance, notes,
                         version, is_current, locked, locked_by_user, locked_timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, CURRENT_TIMESTAMP)
                    """, (
                        reconciliation_date, month, year,
                        str(ledger_total), str(cashbook_total), str(bank_balance),
                        str(variance), notes or current.get('notes'),
                        new_version, locked_by,
                    ))
                    rec_id = cursor.lastrowid
                    action = 'new_version'
                    version = new_version
                elif is_relock_after_unlock and not figures_changed:
                    cursor.execute("""
                        UPDATE reconciliations
                        SET locked = 1,
                            locked_by_user = ?,
                            locked_timestamp = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (locked_by, current['id']))
                    rec_id = current['id']
                    action = 'relocked_unchanged'
                    version = int(current.get('version') or 1)
                else:
                    cursor.execute("""
                        UPDATE reconciliations
                        SET reconciliation_date = ?,
                            ledger_total = ?,
                            cashbook_total = ?,
                            bank_balance = ?,
                            variance = ?,
                            notes = COALESCE(?, notes),
                            version = COALESCE(version, 1),
                            locked = 1,
                            locked_by_user = ?,
                            locked_timestamp = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (
                        reconciliation_date,
                        str(ledger_total), str(cashbook_total), str(bank_balance),
                        str(variance), notes,
                        locked_by, current['id'],
                    ))
                    rec_id = current['id']
                    action = 'locked'
                    version = int(current.get('version') or 1)

                cursor.execute("""
                    INSERT INTO month_locks (lock_month, lock_year, locked, locked_by, locked_at, unlocked_by, unlocked_at)
                    VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP, NULL, NULL)
                    ON CONFLICT(lock_month, lock_year) DO UPDATE SET
                        locked = 1,
                        locked_by = excluded.locked_by,
                        locked_at = CURRENT_TIMESTAMP,
                        unlocked_by = NULL,
                        unlocked_at = NULL
                """, (month, year, locked_by))
                conn.commit()
                return {
                    'action': action,
                    'id': rec_id,
                    'version': version,
                    'month': month,
                    'year': year,
                }
            except sqlite3.IntegrityError as e:
                conn.rollback()
                raise ValueError(f"Could not lock reconciliation: {e}")
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('lock_reconciliation_month', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def unlock_reconciliation_month(self, month: int, year: int, unlocked_by: str) -> Dict:
        """Unlock month for corrections; current version returns to live calculation mode."""
        if not self.is_month_locked(month, year):
            raise ValueError(f'{year}-{month:02d} is not locked.')

        current = self.get_current_reconciliation(month, year)
        if not current:
            raise ValueError('No current reconciliation record exists for this month.')

        self.unlock_month(month, year, unlocked_by)

        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE reconciliations SET locked = 0 WHERE id = ?
            """, (current['id'],))
            conn.commit()
        finally:
            conn.close()

        return {
            'id': current['id'],
            'version': int(current.get('version') or 1),
            'month': month,
            'year': year,
        }

    def _ensure_month_unlocked(self, transaction_date: str):
        dt = datetime.strptime(transaction_date, '%Y-%m-%d')
        if self.is_month_locked(dt.month, dt.year):
            raise ValueError(f"Month {dt.strftime('%B %Y')} is locked. Unlock it before making changes.")

    def reset_unlocked_calendar_month_client_money(
        self,
        year: int,
        month: int,
        expected_client_money_net: Decimal,
        confirm: bool,
        performed_by: str,
        require_months_locked: Optional[List[Tuple[int, int]]] = None,
    ) -> Dict[str, int]:
        """
        Soft reset all financial activity in [year-month] only (inclusive dates).

        Marks January (or any target month) rows as is_deleted=1 in ledger, linked client cashbook,
        office_fee_transfers, office_cashbook, reconciliations, and month-dated audit rows.
        Queries that drive balances/reports exclude soft-deleted rows.

        Does NOT delete clients, users, month_locks, or rows dated outside the range.

        Safety:
        - confirm must be True
        - Environment variable ALLOW_JANUARY_RESET must be exactly 'true' (case-insensitive)
        - Target month must NOT be locked (is_month_locked False)
        - If require_months_locked is set, each (month, year) listed must be locked

        After soft reset, client ledger total and client-linked cleared cashbook total must equal
        expected_client_money_net within £0.02 or the transaction is rolled back.
        """
        if confirm is not True:
            raise ValueError('reset_unlocked_calendar_month_client_money requires confirm=True.')
        if os.environ.get('ALLOW_JANUARY_RESET', '').strip().lower() != 'true':
            raise RuntimeError(
                'Period reset is disabled. Set environment variable ALLOW_JANUARY_RESET=true to enable.'
            )
        if self.is_month_locked(month, year):
            raise ValueError(
                f'Cannot reset {year}-{month:02d}: that month is locked.'
            )
        if require_months_locked:
            for m, y in require_months_locked:
                if not self.is_month_locked(m, y):
                    raise ValueError(
                        f'Required locked month {calendar.month_name[m]} {y} is not locked — '
                        f'refuse to reset {calendar.month_name[month]} {year} for SRA safety.'
                    )

        last_day = calendar.monthrange(year, month)[1]
        d_start = f'{year:04d}-{month:02d}-01'
        d_end = f'{year:04d}-{month:02d}-{last_day:02d}'

        counts: Dict[str, int] = {}
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('BEGIN IMMEDIATE')

                cursor.execute(
                    """
                    UPDATE office_fee_transfers
                    SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, deleted_by = ?, deleted_reason = ?
                    WHERE (transaction_date >= ? AND transaction_date <= ?)
                       OR ledger_transaction_id IN (
                            SELECT id FROM ledger_transactions
                            WHERE transaction_date >= ? AND transaction_date <= ?)
                    """,
                    (performed_by, f'SOFT_RESET_{year:04d}_{month:02d}', d_start, d_end, d_start, d_end),
                )
                counts['office_fee_transfers_soft_deleted'] = cursor.rowcount

                cursor.execute(
                    """
                    UPDATE cheque_status_log
                    SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, deleted_by = ?, deleted_reason = ?
                    WHERE cashbook_id IN (
                        SELECT id FROM cashbook_transactions
                        WHERE (transaction_date >= ? AND transaction_date <= ?)
                           OR linked_ledger_id IN (
                                SELECT id FROM ledger_transactions
                                WHERE transaction_date >= ? AND transaction_date <= ?))
                      AND COALESCE(is_deleted, 0) = 0
                    """,
                    (performed_by, f'SOFT_RESET_{year:04d}_{month:02d}', d_start, d_end, d_start, d_end),
                )
                counts['cheque_status_log_soft_deleted'] = cursor.rowcount

                cursor.execute(
                    """
                    UPDATE cashbook_transactions
                    SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, deleted_by = ?, deleted_reason = ?
                    WHERE (transaction_date >= ? AND transaction_date <= ?)
                       OR linked_ledger_id IN (
                            SELECT id FROM ledger_transactions
                            WHERE transaction_date >= ? AND transaction_date <= ?)
                    """,
                    (performed_by, f'SOFT_RESET_{year:04d}_{month:02d}', d_start, d_end, d_start, d_end),
                )
                counts['cashbook_transactions_soft_deleted'] = cursor.rowcount

                cursor.execute(
                    """
                    UPDATE ledger_transactions
                    SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, deleted_by = ?, deleted_reason = ?
                    WHERE transaction_date >= ? AND transaction_date <= ?
                    """,
                    (performed_by, f'SOFT_RESET_{year:04d}_{month:02d}', d_start, d_end),
                )
                counts['ledger_transactions_soft_deleted'] = cursor.rowcount

                cursor.execute(
                    """
                    UPDATE office_cashbook
                    SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, deleted_by = ?, deleted_reason = ?
                    WHERE transaction_date >= ? AND transaction_date <= ?
                    """,
                    (performed_by, f'SOFT_RESET_{year:04d}_{month:02d}', d_start, d_end),
                )
                counts['office_cashbook_soft_deleted'] = cursor.rowcount

                cursor.execute(
                    """
                    UPDATE reconciliations
                    SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, deleted_by = ?, deleted_reason = ?
                    WHERE reconciliation_year = ? AND reconciliation_month = ?
                    """,
                    (performed_by, f'SOFT_RESET_{year:04d}_{month:02d}', year, month),
                )
                counts['reconciliations_soft_deleted'] = cursor.rowcount

                cursor.execute(
                    """
                    UPDATE audit_trail
                    SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, deleted_by = ?, deleted_reason = ?
                    WHERE substr(COALESCE(timestamp, ''), 1, 10) >= ?
                      AND substr(COALESCE(timestamp, ''), 1, 10) <= ?
                    """,
                    (performed_by, f'SOFT_RESET_{year:04d}_{month:02d}', d_start, d_end),
                )
                counts['audit_trail_soft_deleted'] = cursor.rowcount

                cursor.execute(
                    """
                    UPDATE audit_log
                    SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, deleted_by = ?, deleted_reason = ?
                    WHERE substr(COALESCE(timestamp, ''), 1, 10) >= ?
                      AND substr(COALESCE(timestamp, ''), 1, 10) <= ?
                    """,
                    (performed_by, f'SOFT_RESET_{year:04d}_{month:02d}', d_start, d_end),
                )
                counts['audit_log_soft_deleted'] = cursor.rowcount

                cursor.execute(
                    """
                    UPDATE ledger_transactions
                    SET linked_cashbook_id = NULL
                    WHERE is_deleted = 0 AND linked_cashbook_id IN (
                        SELECT id FROM cashbook_transactions WHERE is_deleted = 1
                    )
                    """,
                )
                counts['ledger_unlinked_cashbook'] = cursor.rowcount

                led = self._cursor_sum_all_clients_ledger(cursor, None)
                cb = self._cursor_sum_all_client_cashbook(cursor, None)
                exp = Decimal(str(expected_client_money_net)).quantize(Decimal('0.01'))
                if abs(led - exp) > Decimal('0.02'):
                    conn.rollback()
                    raise ValueError(
                        f'Reset rolled back: client ledger total £{led} does not match expected £{exp} '
                        f'(December closing).'
                    )
                if abs(cb - exp) > Decimal('0.02'):
                    conn.rollback()
                    raise ValueError(
                        f'Reset rolled back: client cashbook total £{cb} does not match expected £{exp} '
                        f'(December closing).'
                    )
                if abs(led - cb) > Decimal('0.02'):
                    conn.rollback()
                    raise ValueError(
                        f'Reset rolled back: ledger £{led} and cashbook £{cb} still diverge after delete.'
                    )

                conn.commit()
                counts['verified_ledger_net'] = str(led)
                counts['verified_cashbook_net'] = str(cb)
                logger.info(
                    'reset_unlocked_calendar_month_client_money %s-%02d by %s verified ledger=%s cashbook=%s',
                    year, month, performed_by, led, cb,
                )
                return counts
            except ValueError:
                conn.rollback()
                raise
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('reset_unlocked_calendar_month_client_money', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f'Database error: {e}') from e
            finally:
                conn.close()
        raise ValueError(f'Database busy after {DB_WRITE_RETRIES} retries: {last_err}')

    def _ensure_matter_open(self, client_id: int):
        client = self.get_client(client_id)
        if not client:
            raise ValueError("Client not found.")
        if (client.get('matter_status') or 'OPEN').upper() == 'CLOSED':
            raise ValueError("Matter is closed. Reopen the matter to add transactions.")

    def set_matter_status(self, client_id: int, status: str, changed_by: str) -> None:
        if status not in ('OPEN', 'CLOSED'):
            raise ValueError("Status must be OPEN or CLOSED.")
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT matter_status FROM clients WHERE id = ?", (client_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise ValueError("Client not found.")
        old_status = row['matter_status'] or 'OPEN'
        cursor.execute("UPDATE clients SET matter_status = ? WHERE id = ?", (status, client_id))
        conn.commit()
        conn.close()
    
    def reserve_next_client_code(self) -> str:
        """Reserve and return next CLC-XXXX-XXXX code. Increments persistent counter. Survives app restarts."""
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM system_config WHERE key = 'client_code_seq'")
                row = cursor.fetchone()
                seq = int(row['value']) if row else 1
                cursor.execute("SELECT client_code FROM clients WHERE client_code LIKE 'CLC-%'")
                for r in cursor.fetchall():
                    try:
                        parts = r['client_code'].split('-')
                        if len(parts) == 3 and len(parts[1]) == 4 and len(parts[2]) == 4:
                            p1, p2 = int(parts[1]), int(parts[2])
                            n = (p1 - 1) * 9999 + p2
                            if n >= seq:
                                seq = n + 1
                    except (ValueError, IndexError, TypeError):
                        pass
                part1 = ((seq - 1) // 9999) + 1
                part2 = ((seq - 1) % 9999) + 1
                code = f"CLC-{part1:04d}-{part2:04d}"
                cursor.execute("UPDATE system_config SET value = ?, updated_date = CURRENT_TIMESTAMP WHERE key = 'client_code_seq'",
                               (str(seq + 1),))
                conn.commit()
                return code
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('reserve_next_client_code', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")
    
    def reserve_next_transaction_id(self) -> str:
        """Atomically generate next TXN-YYYY-NNNNNN. Never reused."""
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                year = datetime.now().year
                cursor.execute("SELECT value FROM system_config WHERE key = 'txn_id_year'")
                row = cursor.fetchone()
                last_year = int(row['value']) if row else 0
                cursor.execute("SELECT value FROM system_config WHERE key = 'txn_id_seq'")
                row = cursor.fetchone()
                seq = int(row['value']) if row else 0
                if year != last_year:
                    seq = 0
                seq += 1
                cursor.execute("UPDATE system_config SET value = ?, updated_date = CURRENT_TIMESTAMP WHERE key = 'txn_id_year'", (str(year),))
                cursor.execute("UPDATE system_config SET value = ?, updated_date = CURRENT_TIMESTAMP WHERE key = 'txn_id_seq'", (str(seq),))
                conn.commit()
                return f"TXN-{year}-{seq:06d}"
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('reserve_next_transaction_id', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def _migrate_transaction_ids(self, cursor):
        """Populate transaction_id for existing records. Run once."""
        cursor.execute("SELECT COUNT(*) FROM ledger_transactions WHERE transaction_id IS NULL OR transaction_id = ''")
        if cursor.fetchone()[0] == 0:
            return  # Already migrated
        # Get max seq from existing TXN IDs
        cursor.execute("SELECT transaction_id FROM ledger_transactions WHERE transaction_id IS NOT NULL AND transaction_id != '' ORDER BY id")
        max_seq = 0
        year = datetime.now().year
        for row in cursor.fetchall():
            tid = row['transaction_id'] or ''
            if tid.startswith(f'TXN-{year}-'):
                try:
                    n = int(tid.split('-')[-1])
                    if n > max_seq:
                        max_seq = n
                except (ValueError, IndexError):
                    pass
        cursor.execute("SELECT value FROM system_config WHERE key = 'txn_id_seq'")
        r = cursor.fetchone()
        seq = max(max_seq, int(r['value']) if r else 0)
        # Assign to ledger (chronological)
        cursor.execute("SELECT id FROM ledger_transactions WHERE transaction_id IS NULL OR transaction_id = '' ORDER BY transaction_date, id")
        for row in cursor.fetchall():
            seq += 1
            txn_id = f"TXN-{year}-{seq:06d}"
            cursor.execute("UPDATE ledger_transactions SET transaction_id = ? WHERE id = ?", (txn_id, row['id']))
        # Cashbook
        cursor.execute("SELECT id FROM cashbook_transactions WHERE transaction_id IS NULL OR transaction_id = '' ORDER BY transaction_date, id")
        for row in cursor.fetchall():
            seq += 1
            txn_id = f"TXN-{year}-{seq:06d}"
            cursor.execute("UPDATE cashbook_transactions SET transaction_id = ? WHERE id = ?", (txn_id, row['id']))
        # Office fee transfers
        cursor.execute("SELECT id FROM office_fee_transfers WHERE transaction_id IS NULL OR transaction_id = '' ORDER BY transaction_date, id")
        for row in cursor.fetchall():
            seq += 1
            txn_id = f"TXN-{year}-{seq:06d}"
            cursor.execute("UPDATE office_fee_transfers SET transaction_id = ? WHERE id = ?", (txn_id, row['id']))
        cursor.execute("UPDATE system_config SET value = ?, updated_date = CURRENT_TIMESTAMP WHERE key = 'txn_id_year'", (str(year),))
        cursor.execute("UPDATE system_config SET value = ?, updated_date = CURRENT_TIMESTAMP WHERE key = 'txn_id_seq'", (str(seq),))

    def get_next_client_code_preview(self) -> str:
        """Return the next CLC-XXXX-XXXX code (preview only, does not increment). For display before form submit."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = 'client_code_seq'")
        row = cursor.fetchone()
        seq = int(row['value']) if row else 1
        cursor.execute("SELECT client_code FROM clients WHERE client_code LIKE 'CLC-%'")
        for r in cursor.fetchall():
            try:
                parts = r['client_code'].split('-')
                if len(parts) == 3 and len(parts[1]) == 4 and len(parts[2]) == 4:
                    p1, p2 = int(parts[1]), int(parts[2])
                    n = (p1 - 1) * 9999 + p2
                    if n >= seq:
                        seq = n + 1
            except (ValueError, IndexError, TypeError):
                pass
        part1 = ((seq - 1) // 9999) + 1
        part2 = ((seq - 1) % 9999) + 1
        conn.close()
        return f"CLC-{part1:04d}-{part2:04d}"
    
    def _generate_next_client_code(self, cursor) -> str:
        """Generate next CLC-XXXX-XXXX code atomically. Caller must be in a transaction."""
        cursor.execute("SELECT value FROM system_config WHERE key = 'client_code_seq'")
        row = cursor.fetchone()
        seq = int(row['value']) if row else 1
        # Ensure seq is above any existing CLC codes (migration)
        cursor.execute("SELECT client_code FROM clients WHERE client_code LIKE 'CLC-%'")
        for r in cursor.fetchall():
            try:
                parts = r['client_code'].split('-')
                if len(parts) == 3 and len(parts[1]) == 4 and len(parts[2]) == 4:
                    p1, p2 = int(parts[1]), int(parts[2])
                    n = (p1 - 1) * 9999 + p2
                    if n >= seq:
                        seq = n + 1
            except (ValueError, IndexError, TypeError):
                pass
        part1 = ((seq - 1) // 9999) + 1
        part2 = ((seq - 1) % 9999) + 1
        code = f"CLC-{part1:04d}-{part2:04d}"
        cursor.execute("UPDATE system_config SET value = ?, updated_date = CURRENT_TIMESTAMP WHERE key = 'client_code_seq'",
                       (str(seq + 1),))
        return code
    
    def create_client_with_auto_code(self, client_name: str, matter_reference: str = None,
                                     description: str = None) -> int:
        """Create client with auto-generated CLC-XXXX-XXXX code. Returns client_id."""
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                client_code = self._generate_next_client_code(cursor)
                cursor.execute("""
                    INSERT INTO clients (client_code, client_name, matter_reference, description)
                    VALUES (?, ?, ?, ?)
                """, (client_code, client_name, matter_reference, description))
                client_id = cursor.lastrowid
                conn.commit()
                try:
                    self._log_audit('clients', client_id, 'INSERT', None,
                                   {'client_code': client_code, 'client_name': client_name})
                except sqlite3.OperationalError:
                    pass
                return client_id
            except sqlite3.IntegrityError:
                conn.rollback()
                raise ValueError(f"Client code collision (retry): {client_code}")
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('create_client_with_auto_code', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")
    
    def create_client(self, client_code: str, client_name: str, 
                     matter_reference: str = None, description: str = None) -> int:
        last_err = None
        for attempt in range(DB_MAX_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO clients (client_code, client_name, matter_reference, description)
                    VALUES (?, ?, ?, ?)
                """, (client_code.upper(), client_name, matter_reference, description))
                client_id = cursor.lastrowid
                conn.commit()  # Commit BEFORE _log_audit to release lock (audit opens new connection)
                try:
                    self._log_audit('clients', client_id, 'INSERT', None, 
                                  {'client_code': client_code, 'client_name': client_name})
                except sqlite3.OperationalError:
                    pass  # Audit is non-critical; client was saved
                return client_id
            except sqlite3.IntegrityError:
                conn.rollback()
                raise ValueError(f"Client code already exists: {client_code}")
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    time.sleep(DB_RETRY_DELAY * (attempt + 1))
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_MAX_RETRIES} retries: {last_err}")
    
    def get_all_clients(self, active_only: bool = True) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        if active_only:
            cursor.execute("SELECT * FROM clients WHERE is_active = 1 ORDER BY client_name")
        else:
            cursor.execute("SELECT * FROM clients ORDER BY client_name")
        clients = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return clients
    
    def get_client(self, client_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    # Fields permitted on client detail amendment (financial identifiers excluded).
    EDITABLE_CLIENT_FIELDS = (
        'client_name', 'address', 'postcode', 'telephone', 'email',
        'contact_person', 'matter_reference', 'description',
    )

    def update_client_details(self, client_id: int, updates: Dict,
                              updated_by: str = 'System') -> Tuple[List[Dict], Dict]:
        """
        Update editable client profile fields. Returns (changes, updated_client).
        Each change: {field, label, old_value, new_value}.
        Raises ValueError on validation failure or missing client.
        """
        existing = self.get_client(client_id)
        if not existing:
            raise ValueError('Client not found.')

        normalized = {}
        for field in self.EDITABLE_CLIENT_FIELDS:
            if field not in updates:
                continue
            val = updates[field]
            if val is None:
                normalized[field] = None
            else:
                stripped = str(val).strip()
                normalized[field] = stripped if stripped else None

        if 'client_name' in normalized and not normalized['client_name']:
            raise ValueError('Client Name is required.')

        old_snapshot = {f: (existing.get(f) or '') for f in self.EDITABLE_CLIENT_FIELDS}
        new_snapshot = dict(old_snapshot)
        for field, val in normalized.items():
            new_snapshot[field] = val or ''

        changes = []
        for field in self.EDITABLE_CLIENT_FIELDS:
            old_v = old_snapshot[field]
            new_v = new_snapshot[field]
            if old_v != new_v:
                changes.append({
                    'field': field,
                    'old_value': old_v,
                    'new_value': new_v,
                })

        if not changes:
            return [], existing

        set_parts = [f"{field} = ?" for field in normalized]
        params = [normalized[field] for field in normalized]
        set_parts.extend(['updated_by = ?', 'updated_date = CURRENT_TIMESTAMP'])
        params.extend([updated_by, client_id])

        last_err = None
        for attempt in range(DB_MAX_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE clients SET {', '.join(set_parts)} WHERE id = ?",
                    params,
                )
                conn.commit()
                try:
                    self._log_audit(
                        'clients', client_id, 'UPDATE',
                        old_snapshot, new_snapshot,
                        reason='Client details amended',
                    )
                except sqlite3.OperationalError:
                    pass
                updated = self.get_client(client_id)
                return changes, updated
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    time.sleep(DB_RETRY_DELAY * (attempt + 1))
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_MAX_RETRIES} retries: {last_err}")

    def _cursor_sum_ledger_client(self, cursor, client_id: int, as_of_date: str = None) -> Decimal:
        """Net client ledger from open cursor (same rules as get_client_balance)."""
        q = """
            SELECT COALESCE(SUM(
                CASE
                    WHEN lt.transaction_type = 'Receipt' THEN lt.amount
                    WHEN lt.transaction_type IN ('Payment', 'Transfer') THEN -lt.amount
                    ELSE 0
                END
            ), 0)
            FROM ledger_transactions lt
            LEFT JOIN cashbook_transactions ct ON lt.linked_cashbook_id = ct.id
            WHERE lt.client_id = ?
              AND (lt.linked_cashbook_id IS NULL OR ct.status = 'Cleared')
              AND COALESCE(lt.is_deleted, 0) = 0
              AND (ct.id IS NULL OR COALESCE(ct.is_deleted, 0) = 0)
        """ + _LEDGER_BALANCE_EFFECTIVE_SQL
        params = [client_id]
        if as_of_date:
            q += " AND lt.transaction_date <= ?"
            params.append(as_of_date)
        cursor.execute(q, params)
        return Decimal(str(cursor.fetchone()[0] or 0))

    def _cursor_sum_ledger_client_through_row(
        self, cursor, client_id: int, through_date: str, through_id: int
    ) -> Decimal:
        """
        Authoritative cumulative client ledger net through (through_date, through_id) inclusive,
        using same rules as get_client_balance. Read-only.
        """
        q = """
            SELECT COALESCE(SUM(
                CASE
                    WHEN lt.transaction_type = 'Receipt' THEN lt.amount
                    WHEN lt.transaction_type IN ('Payment', 'Transfer') THEN -lt.amount
                    ELSE 0
                END
            ), 0)
            FROM ledger_transactions lt
            LEFT JOIN cashbook_transactions ct ON lt.linked_cashbook_id = ct.id
            WHERE lt.client_id = ?
              AND (lt.linked_cashbook_id IS NULL OR ct.status = 'Cleared')
              AND COALESCE(lt.is_deleted, 0) = 0
              AND (ct.id IS NULL OR COALESCE(ct.is_deleted, 0) = 0)
              AND (
                  lt.transaction_date < ?
                  OR (lt.transaction_date = ? AND lt.id <= ?)
              )
        """ + _LEDGER_BALANCE_EFFECTIVE_SQL
        cursor.execute(q, (client_id, through_date, through_date, through_id))
        return Decimal(str(cursor.fetchone()[0] or 0))

    def audit_client_ledger_running_balance(self, client_id: int) -> Dict:
        """
        Read-only forensic audit: compare incremental running total (with 2dp rounding per step)
        to database cumulative balance at each row, and to an independent scratch rebuild.

        Flags the first row where incremental path diverges from SQL truth (or from scratch sum).
        """
        rows_in = self.get_client_transactions(client_id)
        chrono = list(reversed(rows_in))
        q2 = Decimal('0.01')
        running = Decimal('0.00')
        detail: List[Dict] = []
        first_flag: Optional[Dict] = None

        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            for row in chrono:
                d = row.get('transaction_date') or ''
                lid = int(row['id'])
                delta = _ledger_row_signed_delta_for_running(row)
                prev_running = running
                running = (running + delta).quantize(q2, rounding=ROUND_HALF_UP)

                db_cum = self._cursor_sum_ledger_client_through_row(cursor, client_id, d, lid)
                db_cum = db_cum.quantize(q2, rounding=ROUND_HALF_UP)

                diff_inc_db = (running - db_cum).quantize(q2, rounding=ROUND_HALF_UP)

                flagged = abs(diff_inc_db) > Decimal('0.005')
                entry = {
                    'ledger_id': lid,
                    'transaction_id': row.get('transaction_id'),
                    'transaction_date': d,
                    'transaction_type': row.get('transaction_type'),
                    'amount': str(row.get('amount')),
                    'delta_applied': str(delta),
                    'balance_before': str(prev_running),
                    'running_incremental': str(running),
                    'database_authoritative': str(db_cum),
                    'difference': str(diff_inc_db),
                    'flagged': flagged,
                }
                detail.append(entry)
                if flagged and first_flag is None:
                    first_flag = entry
        finally:
            conn.close()

        final_db = self.get_client_balance(client_id).quantize(q2, rounding=ROUND_HALF_UP)
        final_running = running
        final_diff = (final_running - final_db).quantize(q2, rounding=ROUND_HALF_UP)

        return {
            'client_id': client_id,
            'row_count': len(chrono),
            'rows': detail,
            'first_discrepancy': first_flag,
            'final_running_incremental': str(final_running),
            'final_database_balance': str(final_db),
            'final_difference': str(final_diff),
            'final_ok': abs(final_diff) <= Decimal('0.005'),
        }

    def _cursor_sum_cashbook_client(self, cursor, client_id: int, as_of_date: str = None) -> Decimal:
        """Net client-linked cleared cashbook for one client (matches ledger rules)."""
        q = """
            SELECT COALESCE(SUM(
                CASE
                    WHEN cb.transaction_type = 'Receipt' THEN cb.amount
                    WHEN cb.transaction_type = 'Payment' THEN -cb.amount
                    ELSE 0
                END
            ), 0)
            FROM cashbook_transactions cb
            INNER JOIN ledger_transactions lt ON cb.linked_ledger_id = lt.id
            WHERE lt.client_id = ?
              AND cb.status = 'Cleared'
              AND COALESCE(cb.is_deleted, 0) = 0
              AND COALESCE(lt.is_deleted, 0) = 0
        """ + _CASHBOOK_BALANCE_EFFECTIVE_SQL_CB
        params = [client_id]
        if as_of_date:
            q += " AND cb.transaction_date <= ?"
            params.append(as_of_date)
        cursor.execute(q, params)
        return Decimal(str(cursor.fetchone()[0] or 0))

    def _cursor_sum_all_clients_ledger(self, cursor, as_of_date: str = None) -> Decimal:
        """Total client ledger net (all clients), same rules as get_total_ledger_balance."""
        q = """
            SELECT COALESCE(SUM(
                CASE
                    WHEN lt.transaction_type = 'Receipt' THEN lt.amount
                    WHEN lt.transaction_type IN ('Payment', 'Transfer') THEN -lt.amount
                    ELSE 0
                END
            ), 0)
            FROM ledger_transactions lt
            LEFT JOIN cashbook_transactions ct ON lt.linked_cashbook_id = ct.id
            WHERE (lt.linked_cashbook_id IS NULL OR ct.status = 'Cleared')
              AND COALESCE(lt.is_deleted, 0) = 0
              AND (ct.id IS NULL OR COALESCE(ct.is_deleted, 0) = 0)
        """ + _LEDGER_BALANCE_EFFECTIVE_SQL
        params = []
        if as_of_date:
            q += " AND lt.transaction_date <= ?"
            params.append(as_of_date)
        cursor.execute(q, params)
        return Decimal(str(cursor.fetchone()[0] or 0))

    def _cursor_sum_all_client_cashbook(self, cursor, as_of_date: str = None) -> Decimal:
        """Total cleared client-linked cashbook net (matches bank client_only sum)."""
        q = """
            SELECT COALESCE(SUM(
                CASE
                    WHEN transaction_type = 'Receipt' THEN amount
                    WHEN transaction_type = 'Payment' THEN -amount
                    ELSE 0
                END
            ), 0)
            FROM cashbook_transactions
            WHERE status = 'Cleared'
              AND linked_ledger_id IS NOT NULL
              AND COALESCE(is_deleted, 0) = 0
              AND linked_ledger_id IN (
                  SELECT id FROM ledger_transactions WHERE COALESCE(is_deleted, 0) = 0
              )
        """ + _CASHBOOK_BALANCE_EFFECTIVE_SQL
        params = []
        if as_of_date:
            q += " AND transaction_date <= ?"
            params.append(as_of_date)
        cursor.execute(q, params)
        return Decimal(str(cursor.fetchone()[0] or 0))

    def get_client_balance(self, client_id: int, as_of_date: str = None) -> Decimal:
        """
        Client balance from CLEARED ledger rows that count toward the running position.

        Excludes pending cheques, REVERSED rows, and compensating legs at odd reversal_depth
        (depth 1, 3, …). Even-depth legs (0, 2, …) count so re-reversals restore balances.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            return self._cursor_sum_ledger_client(cursor, client_id, as_of_date)
        finally:
            conn.close()

    def get_cleared_client_balance(self, client_id: int, as_of_date: str = None) -> Decimal:
        """Same rules as get_client_balance (cleared-linked cheque filter + reversal_depth parity)."""
        return self.get_client_balance(client_id, as_of_date)

    def get_client_cashbook_net_balance(self, client_id: int, as_of_date: str = None) -> Decimal:
        """Cleared client-money cashbook net for one client (linked rows only), same parity as ledger."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            return self._cursor_sum_cashbook_client(cursor, client_id, as_of_date)
        finally:
            conn.close()

    def check_deficit(self, client_id: int, amount: Decimal, transaction_type: str, 
                     as_of_date: str = None) -> bool:
        """Uses CLEARED balance. Payment/Transfer blocked if insufficient cleared funds."""
        current_balance = self.get_cleared_client_balance(client_id, as_of_date)
        if transaction_type == 'Payment':
            return (current_balance - amount) < Decimal('0')
        elif transaction_type == 'Transfer':
            return current_balance < amount
        return False
    
    def get_total_cashbook_net_balance(self) -> Decimal:
        """Total cleared client-linked cashbook balance across all clients."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            return self._cursor_sum_all_client_cashbook(cursor)
        finally:
            conn.close()

    def create_ledger_transaction(self, client_id: int, transaction_date: str,
                                 amount: Decimal, transaction_type: str, reference: str,
                                 source: str, description: str = None,
                                 linked_cashbook_id: int = None,
                                 created_by: str = 'System',
                                 allow_override: bool = False) -> int:
        self._ensure_matter_open(client_id)
        if transaction_type in ('Payment', 'Transfer'):
            if allow_override:
                total_cashbook = self.get_total_cashbook_net_balance()
                if (total_cashbook - amount) < Decimal('0'):
                    raise ValueError(
                        f"Override blocked: transaction would reduce total client cashbook below £0. "
                        f"Total cashbook: £{total_cashbook:,.2f} | Transaction: £{amount:,.2f}")
            else:
                if self.check_deficit(client_id, amount, transaction_type, transaction_date):
                    raise ValueError("Payment exceeds cleared client funds.")
        if not reference or not reference.strip():
            raise ValueError("Reference is mandatory")
        if not source:
            raise ValueError("Transaction source is mandatory")
        self._ensure_month_unlocked(transaction_date)
        
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                txn_id = self.reserve_next_transaction_id()
                cursor.execute("""
                    INSERT INTO ledger_transactions 
                    (transaction_id, client_id, transaction_date, amount, transaction_type, reference, 
                     source, description, linked_cashbook_id, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (txn_id, client_id, transaction_date, str(amount), transaction_type, 
                      reference, source, description, linked_cashbook_id, created_by))
                ledger_row_id = cursor.lastrowid
                conn.commit()  # Commit BEFORE _log_audit to release lock
                try:
                    self._log_audit('ledger_transactions', ledger_row_id, 'INSERT', None,
                                  {'transaction_id': txn_id, 'client_id': client_id, 'amount': str(amount), 
                                   'transaction_type': transaction_type, 'reference': reference})
                except sqlite3.OperationalError:
                    pass
                return ledger_row_id
            except sqlite3.IntegrityError:
                conn.rollback()
                raise
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('create_ledger_transaction', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def create_ledger_and_cashbook_transaction(self, client_id: int, transaction_date: str,
                                               amount: Decimal, transaction_type: str,
                                               reference: str, source: str,
                                               description: str = None,
                                               created_by: str = 'System',
                                               allow_override: bool = False) -> tuple:
        """
        Atomically create BOTH ledger and cashbook entries. Client Ledger transactions
        must sync to Cashbook in real time. Cheque = Pending, others = Cleared.
        Returns (ledger_id, cashbook_id). Rolls back both if either fails.
        """
        self._ensure_matter_open(client_id)
        if transaction_type in ('Payment', 'Transfer'):
            if allow_override:
                total_cashbook = self.get_total_cashbook_net_balance()
                if (total_cashbook - amount) < Decimal('0'):
                    raise ValueError(
                        f"Override blocked: transaction would reduce total client cashbook below £0. "
                        f"Total cashbook: £{total_cashbook:,.2f} | Transaction: £{amount:,.2f}")
            else:
                if self.check_deficit(client_id, amount, transaction_type, transaction_date):
                    raise ValueError("Payment exceeds cleared client funds.")
        if not reference or not reference.strip():
            raise ValueError("Reference is mandatory")
        if not source:
            raise ValueError("Transaction source is mandatory")
        self._ensure_month_unlocked(transaction_date)

        cashbook_type = 'Payment' if transaction_type in ('Payment', 'Transfer') else 'Receipt'
        status = 'Pending' if source == 'Cheque' else 'Cleared'

        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                txn_ledger = self.reserve_next_transaction_id()
                txn_cashbook = self.reserve_next_transaction_id()
                cursor.execute("""
                    INSERT INTO ledger_transactions
                    (transaction_id, client_id, transaction_date, amount, transaction_type, reference,
                     source, description, linked_cashbook_id, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (txn_ledger, client_id, transaction_date, str(amount), transaction_type,
                      reference, source, description, None, created_by))
                ledger_id = cursor.lastrowid

                cursor.execute("""
                    INSERT INTO cashbook_transactions
                    (transaction_id, transaction_date, amount, transaction_type, reference, source,
                     description, status, linked_ledger_id, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (txn_cashbook, transaction_date, str(amount), cashbook_type, reference,
                      source, description, status, ledger_id, created_by))
                cashbook_id = cursor.lastrowid

                if source == 'Cheque' and status == 'Pending':
                    clearance_days = int(self.get_config('cheque_clearance_days', '5'))
                    cleared_date = (datetime.strptime(transaction_date, '%Y-%m-%d') +
                                   timedelta(days=clearance_days)).strftime('%Y-%m-%d')
                    cursor.execute("""
                        UPDATE cashbook_transactions SET cleared_date = ? WHERE id = ?
                    """, (cleared_date, cashbook_id))

                cursor.execute("UPDATE ledger_transactions SET linked_cashbook_id = ? WHERE id = ?",
                               (cashbook_id, ledger_id))

                conn.commit()
                try:
                    self._log_audit('ledger_transactions', ledger_id, 'INSERT', None,
                                   {'transaction_id': txn_ledger, 'client_id': client_id, 'amount': str(amount),
                                    'transaction_type': transaction_type, 'reference': reference})
                    self._log_audit('cashbook_transactions', cashbook_id, 'INSERT', None,
                                   {'transaction_id': txn_cashbook, 'amount': str(amount), 'transaction_type': cashbook_type,
                                    'reference': reference, 'status': status})
                except sqlite3.OperationalError:
                    pass
                return ledger_id, cashbook_id, txn_ledger
            except sqlite3.IntegrityError:
                conn.rollback()
                raise
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('create_ledger_and_cashbook_transaction', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")
    
    def get_client_transactions(self, client_id: int, start_date: str = None,
                               end_date: str = None, created_by: str = None) -> List[Dict]:
        """Get client ledger transactions with cashbook clearance status."""
        conn = self.get_connection()
        cursor = conn.cursor()
        query = """
            SELECT lt.*, c.client_code, c.client_name,
                   ct.status as cashbook_status,
                   ct.cleared_date as cashbook_cleared_date
            FROM ledger_transactions lt
            JOIN clients c ON lt.client_id = c.id
            LEFT JOIN cashbook_transactions ct ON lt.linked_cashbook_id = ct.id
            WHERE lt.client_id = ?
              AND COALESCE(lt.is_deleted, 0) = 0
        """
        params = [client_id]
        if start_date:
            query += " AND lt.transaction_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND lt.transaction_date <= ?"
            params.append(end_date)
        if created_by:
            query += " AND lt.created_by = ?"
            params.append(created_by)
        query += " ORDER BY lt.transaction_date DESC, lt.id DESC"
        cursor.execute(query, params)
        transactions = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return transactions
    
    def get_ledger_transactions_for_report(self, client_id: int = None,
                                           start_date: str = None,
                                           end_date: str = None,
                                           created_by: str = None) -> List[Dict]:
        """Get ledger transactions for reports (single client or all clients) with cashbook status."""
        if client_id:
            return self.get_client_transactions(client_id, start_date, end_date, created_by)
        conn = self.get_connection()
        cursor = conn.cursor()
        query = """
            SELECT lt.*, c.client_code, c.client_name,
                   ct.status as cashbook_status,
                   ct.cleared_date as cashbook_cleared_date
            FROM ledger_transactions lt
            JOIN clients c ON lt.client_id = c.id
            LEFT JOIN cashbook_transactions ct ON lt.linked_cashbook_id = ct.id
            WHERE COALESCE(lt.is_deleted, 0) = 0
        """
        params = []
        if start_date:
            query += " AND lt.transaction_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND lt.transaction_date <= ?"
            params.append(end_date)
        if created_by:
            query += " AND lt.created_by = ?"
            params.append(created_by)
        query += " ORDER BY c.client_name, lt.transaction_date DESC, lt.id DESC"
        cursor.execute(query, params)
        transactions = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return transactions
    
    def create_cashbook_transaction(self, transaction_date: str, amount: Decimal,
                                   transaction_type: str, reference: str, source: str,
                                   description: str = None, linked_ledger_id: int = None,
                                   created_by: str = 'System') -> int:
        """Create client cashbook entry only. Office transactions must use create_office_transaction."""
        if linked_ledger_id is None:
            raise ValueError("Office transactions are not allowed in client cashbook. Use Add Office Income/Expense from Office Account.")
        status = 'Pending' if source == 'Cheque' else 'Cleared'
        if not reference or not reference.strip():
            raise ValueError("Reference is mandatory")
        self._ensure_month_unlocked(transaction_date)
        
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                txn_id = self.reserve_next_transaction_id()
                cursor.execute("""
                    INSERT INTO cashbook_transactions
                    (transaction_id, transaction_date, amount, transaction_type, reference, source,
                     description, status, linked_ledger_id, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (txn_id, transaction_date, str(amount), transaction_type, reference,
                      source, description, status, linked_ledger_id, created_by))
                cb_row_id = cursor.lastrowid
                if source == 'Cheque' and status == 'Pending':
                    clearance_days = int(self.get_config('cheque_clearance_days', '5'))
                    cleared_date = (datetime.strptime(transaction_date, '%Y-%m-%d') + 
                                   timedelta(days=clearance_days)).strftime('%Y-%m-%d')
                    cursor.execute("""
                        UPDATE cashbook_transactions SET cleared_date = ? WHERE id = ?
                    """, (cleared_date, cb_row_id))
                conn.commit()  # Commit BEFORE _log_audit to release lock
                try:
                    self._log_audit('cashbook_transactions', cb_row_id, 'INSERT', None,
                                  {'transaction_id': txn_id, 'amount': str(amount), 'transaction_type': transaction_type,
                                   'reference': reference, 'status': status})
                except sqlite3.OperationalError:
                    pass
                return cb_row_id
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('create_cashbook_transaction', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def create_office_transaction(self, transaction_date: str, amount: Decimal,
                                  transaction_type: str, reference: str, source: str,
                                  description: str = None, created_by: str = 'System') -> int:
        """
        Create office-only transaction. NEVER touches client ledger or cashbook_transactions.
        client_id and matter_id are implicitly NULL (office account only).
        """
        if not reference or not reference.strip():
            raise ValueError("Reference is mandatory")
        if transaction_type == 'Payment':
            office_balance = self.get_office_balance(as_of_date=transaction_date)
            if office_balance - amount < Decimal('0'):
                raise ValueError("Office account balance cannot go below £0.")
        self._ensure_month_unlocked(transaction_date)

        status = 'Pending' if source == 'Cheque' else 'Cleared'
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                txn_id = self.reserve_next_transaction_id()
                cursor.execute("""
                    INSERT INTO office_cashbook
                    (transaction_id, transaction_date, amount, transaction_type, reference, source,
                     description, status, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (txn_id, transaction_date, str(amount), transaction_type, reference,
                      source, description, status, created_by))
                row_id = cursor.lastrowid
                if source == 'Cheque' and status == 'Pending':
                    clearance_days = int(self.get_config('cheque_clearance_days', '5'))
                    cleared_date = (datetime.strptime(transaction_date, '%Y-%m-%d') +
                                   timedelta(days=clearance_days)).strftime('%Y-%m-%d')
                    cursor.execute(
                        "UPDATE office_cashbook SET cleared_date = ? WHERE id = ?",
                        (cleared_date, row_id)
                    )
                conn.commit()
                try:
                    self._log_audit('office_cashbook', row_id, 'INSERT', None,
                                   {'transaction_id': txn_id, 'amount': str(amount),
                                    'transaction_type': transaction_type, 'reference': reference,
                                    'status': status})
                except sqlite3.OperationalError:
                    pass
                return row_id
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('create_office_transaction', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def update_office_cashbook_status(self, office_cashbook_id: int, new_status: str, 
                                      reason: str = None, changed_by: str = 'System'):
        """
        Update office cheque status. Only allowed for Cheque transactions.
        
        Business rules:
        - Pending → Cleared: Office funds now count in balance
        - Pending → Declined: Cheque rejected, no balance impact
        - Cleared → Declined: NOT ALLOWED (must use reversal process)
        - Decline requires reason
        """
        if new_status == 'Declined' and (not reason or not str(reason).strip()):
            raise ValueError("Decline reason is mandatory. Please provide a reason.")

        # Phase 1: Read and validate
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM office_cashbook WHERE id = ?", (office_cashbook_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("Office transaction not found")
            old_row = dict(row)
            if old_row['source'] != 'Cheque':
                raise ValueError("Status updates are only allowed for Cheque transactions. Non-cheque transactions are final and locked.")
            if old_row['status'] == new_status:
                return
            if new_status not in ('Cleared', 'Declined'):
                raise ValueError("Invalid status. Use Cleared or Declined.")
            
            # Prevent declining a cleared cheque
            if old_row['status'] == 'Cleared' and new_status == 'Declined':
                raise ValueError("Cannot decline a cleared cheque. Use the Reverse Transaction feature instead.")
            
            self._ensure_month_unlocked(old_row['transaction_date'])
        finally:
            conn.close()

        # Phase 2: Execute writes
        update_date = datetime.now().strftime('%Y-%m-%d')
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                if new_status == 'Declined':
                    cursor.execute("""
                        UPDATE office_cashbook 
                        SET status = ?, cleared_date = NULL, declined_by = ?, 
                            declined_at = CURRENT_TIMESTAMP, decline_reason = ? 
                        WHERE id = ?
                    """, (new_status, changed_by, (reason or '').strip()[:500], office_cashbook_id))
                else:
                    cursor.execute("""
                        UPDATE office_cashbook SET status = ?, cleared_date = ? WHERE id = ?
                    """, (new_status, update_date, office_cashbook_id))
                conn.commit()
                try:
                    self._log_audit('office_cashbook', office_cashbook_id, 'UPDATE', old_row,
                                   {'status': new_status}, reason=reason)
                except sqlite3.OperationalError:
                    pass
                return
            except ValueError:
                conn.rollback()
                raise
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('update_office_cashbook_status', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def update_ledger_linked_cashbook(self, ledger_id: int, cashbook_id: int):
        """Link a ledger transaction to its cashbook entry (bidirectional)."""
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE ledger_transactions SET linked_cashbook_id = ? WHERE id = ?",
                               (cashbook_id, ledger_id))
                conn.commit()
                return
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('update_ledger_linked_cashbook', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def update_cashbook_status(self, cashbook_id: int, new_status: str, reason: str = None,
                               changed_by: str = 'System'):
        """
        Update cheque status. Only allowed for Cheque transactions.
        
        Business rules:
        - Pending → Cleared: Cheque funds now count in client balance
        - Pending → Declined: Cheque rejected, NO reversal needed (balance never changed)
        - Cleared → Declined: NOT ALLOWED (must use full reversal process)
        - Decline requires reason
        """
        if new_status == 'Declined' and (not reason or not str(reason).strip()):
            raise ValueError("Decline reason is mandatory. Please provide a reason.")

        # Phase 1: Read data and validate state transition
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cashbook_transactions WHERE id = ?", (cashbook_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("Transaction not found")
            old_row = dict(row)
            if old_row['source'] != 'Cheque':
                raise ValueError("Status updates are only allowed for Cheque transactions. Non-cheque transactions are final and locked.")
            if old_row['status'] == new_status:
                return
            if new_status not in ('Cleared', 'Declined'):
                raise ValueError("Invalid status. Use Cleared or Declined.")
            
            # Prevent declining a cleared cheque (must use reversal process for cleared transactions)
            if old_row['status'] == 'Cleared' and new_status == 'Declined':
                raise ValueError("Cannot decline a cleared cheque. Use the Reverse Transaction feature instead.")
            
            self._ensure_month_unlocked(old_row['transaction_date'])
        finally:
            conn.close()

        # Phase 2: No reversal needed - pending cheques never affected balance
        # (Balance only counts cleared transactions, so declining a pending cheque has no balance impact)

        # Phase 3: Execute writes in a single transaction
        update_date = datetime.now().strftime('%Y-%m-%d')
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                if new_status == 'Declined':
                    cursor.execute("""
                        UPDATE cashbook_transactions SET status = ?, cleared_date = ?, declined_by = ?, declined_at = CURRENT_TIMESTAMP, decline_reason = ? WHERE id = ?
                    """, (new_status, None, changed_by, (reason or '').strip()[:500], cashbook_id))
                else:
                    cursor.execute("""
                        UPDATE cashbook_transactions SET status = ?, cleared_date = ? WHERE id = ?
                    """, (new_status, update_date, cashbook_id))
                cursor.execute("""
                    INSERT INTO cheque_status_log (cashbook_id, previous_status, new_status, changed_by)
                    VALUES (?, ?, ?, ?)
                """, (cashbook_id, old_row['status'], new_status, changed_by))
                conn.commit()
                try:
                    self._log_audit('cashbook_transactions', cashbook_id, 'UPDATE', old_row,
                                   {'status': new_status}, reason=reason)
                except sqlite3.OperationalError:
                    pass
                return
            except ValueError:
                conn.rollback()
                raise
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('update_cashbook_status', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")
    
    def get_bank_balance(self, as_of_date: str = None, client_only: bool = False) -> Decimal:
        """
        Bank balance from CLEARED, non-REVERSED cashbook transactions.
        When client_only=True, sums only client-linked (Client Money).
        
        Excludes:
        - Pending/Declined transactions
        - REVERSED transactions (original entries that have been reversed)
        - REVERSAL entries (compensating entries for audit trail only)
        
        After a reversal: Original + Reversal = Net Zero financial impact.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        query = """
            SELECT COALESCE(SUM(
                CASE
                    WHEN transaction_type = 'Receipt' AND status = 'Cleared' THEN amount
                    WHEN transaction_type = 'Payment' AND status = 'Cleared' THEN -amount
                    ELSE 0
                END
            ), 0) as balance
            FROM cashbook_transactions
            WHERE status = 'Cleared'
              AND COALESCE(is_deleted, 0) = 0
        """ + _CASHBOOK_BALANCE_EFFECTIVE_SQL
        params = []
        if client_only:
            query += (
                " AND linked_ledger_id IS NOT NULL"
                " AND linked_ledger_id IN ("
                "SELECT id FROM ledger_transactions WHERE COALESCE(is_deleted, 0) = 0)"
            )
        if as_of_date:
            query += " AND transaction_date <= ?"
            params.append(as_of_date)
        cursor.execute(query, params)
        result = cursor.fetchone()
        balance = Decimal(str(result['balance'] or 0))
        conn.close()
        return balance
    
    def get_all_cashbook_transactions(self, start_date: str = None,
                                     end_date: str = None, status: str = None,
                                     created_by: str = None, client_only: bool = False) -> List[Dict]:
        """
        Returns cashbook transactions. When client_only=True, returns only client-linked
        (Client Money Cashbook). Office transactions live in office_cashbook and never appear here.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        query = """
            SELECT ct.*,
                   lt.client_id,
                   c.client_code,
                   c.client_name
            FROM cashbook_transactions ct
            LEFT JOIN ledger_transactions lt ON ct.linked_ledger_id = lt.id
            LEFT JOIN clients c ON lt.client_id = c.id
            WHERE COALESCE(ct.is_deleted, 0) = 0
        """
        params = []
        if client_only:
            query += (
                " AND ct.linked_ledger_id IS NOT NULL"
                " AND COALESCE(lt.is_deleted, 0) = 0"
            )
        if start_date:
            query += " AND ct.transaction_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND ct.transaction_date <= ?"
            params.append(end_date)
        if status:
            query += " AND ct.status = ?"
            params.append(status)
        if created_by:
            query += " AND ct.created_by = ?"
            params.append(created_by)
        query += " ORDER BY ct.transaction_date DESC, ct.id DESC"
        cursor.execute(query, params)
        transactions = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return transactions
    
    def create_reconciliation(self, reconciliation_date: str, ledger_total: Decimal,
                            cashbook_total: Decimal, bank_balance: Decimal,
                            notes: str = None) -> int:
        from reconciliation_utils import compute_reconciliation_state

        state = compute_reconciliation_state(ledger_total, cashbook_total, bank_balance)
        if not state['can_complete']:
            raise ValueError(
                'Cannot complete reconciliation: Client Ledger, Cashbook, and bank balance must '
                'agree within £0.01. Resolve differences before recording.'
            )

        ledger_total = state['ledger_total']
        cashbook_total = state['cashbook_total']
        bank_balance = state['bank_balance']
        variance = state['variance']

        rec_date = datetime.strptime(reconciliation_date, '%Y-%m-%d')
        month = rec_date.month
        year = rec_date.year

        existing = self.get_current_reconciliation(month, year)
        if existing:
            if existing.get('locked') or self.is_month_locked(month, year):
                raise ValueError(
                    f'A reconciliation for {rec_date.strftime("%B %Y")} already exists. '
                    'Unlock the month to revise and create a new version.'
                )
            raise ValueError(
                f'A reconciliation for {rec_date.strftime("%B %Y")} is already open. '
                'Make any corrections, then use Lock Reconciliation to finalise. '
                'After unlocking a locked month, use Lock Reconciliation to create the next version.'
            )

        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO reconciliations
                    (reconciliation_date, reconciliation_month, reconciliation_year,
                     ledger_total, cashbook_total, bank_balance, variance, notes,
                     version, is_current, locked)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0)
                """, (reconciliation_date, month, year, str(ledger_total),
                      str(cashbook_total), str(bank_balance), str(variance), notes))
                rec_id = cursor.lastrowid
                conn.commit()
                try:
                    self._log_audit('reconciliations', rec_id, 'INSERT', None,
                                  {'month': month, 'year': year, 'version': 1, 'variance': str(variance)})
                except sqlite3.OperationalError:
                    pass
                return rec_id
            except sqlite3.IntegrityError:
                conn.rollback()
                raise ValueError(
                    f'A current reconciliation for {rec_date.strftime("%B %Y")} already exists.'
                )
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('create_reconciliation', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def save_reconciliation_bank_session(self, user_id: int, bank_entries: list = None,
                                         matching_results: list = None,
                                         date_from: str = None, date_to: str = None,
                                         manual_review: bool = None):
        """Store bank upload and/or matching results. Only updates provided fields."""
        existing = self.get_reconciliation_bank_session(user_id) or {}
        entries = bank_entries if bank_entries is not None else existing.get('bank_entries', [])
        results = matching_results if matching_results is not None else existing.get('matching_results', [])
        df = date_from if date_from is not None else existing.get('date_from')
        dt = date_to if date_to is not None else existing.get('date_to')
        mr = manual_review if manual_review is not None else existing.get('manual_review', False)
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO reconciliation_bank_session (user_id, bank_entries_json, matching_results_json, date_from, date_to, manual_review, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    bank_entries_json = excluded.bank_entries_json,
                    matching_results_json = excluded.matching_results_json,
                    date_from = excluded.date_from,
                    date_to = excluded.date_to,
                    manual_review = excluded.manual_review,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, json.dumps(entries), json.dumps(results), df, dt, 1 if mr else 0))
            conn.commit()
        finally:
            conn.close()

    def get_reconciliation_bank_session(self, user_id: int) -> Optional[Dict]:
        """Retrieve bank session for user."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM reconciliation_bank_session WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        if d.get('bank_entries_json'):
            d['bank_entries'] = json.loads(d['bank_entries_json'])
        else:
            d['bank_entries'] = []
        if d.get('matching_results_json'):
            d['matching_results'] = json.loads(d['matching_results_json'])
        else:
            d['matching_results'] = []
        d['manual_review'] = bool(d.get('manual_review', 0))
        return d

    def clear_reconciliation_bank_session(self, user_id: int):
        """Clear bank session after reconciliation or on demand."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reconciliation_bank_session WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    
    def create_transfer_fee_to_office(self, client_id: int, transaction_date: str, amount: Decimal,
                                      reference: str, description: str = None,
                                      created_by: str = 'System') -> dict:
        """
        Atomically record a fee transfer to the office account (SRA-aligned bank movement).

        Creates in a single database transaction:
        - Client ledger Payment (reduces client funds)
        - Linked client cashbook Payment, Cleared, Bank Transfer (reduces client/bank balance)
        - office_fee_transfers row (office income)

        Rolls back fully if any insert fails. Uses the same validation as a normal Payment.
        """
        self._ensure_matter_open(client_id)
        if self.check_deficit(client_id, amount, 'Payment', transaction_date):
            raise ValueError("Payment exceeds cleared client funds.")
        if not reference or not reference.strip():
            raise ValueError("Reference is mandatory")
        self._ensure_month_unlocked(transaction_date)

        source = 'Bank Transfer'
        ledger_desc = (description or '').strip() or 'Fee transfer to office account'
        cashbook_line = (description or '').strip() or 'Transfer to Office – Costs'

        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, client_code, matter_reference FROM clients WHERE id = ?",
                    (client_id,),
                )
                crow = cursor.fetchone()
                if not crow:
                    raise ValueError("Client not found.")
                client_code = crow['client_code']
                matter_ref = (crow['matter_reference'] or '').strip()

                cashbook_desc = f"{cashbook_line} | Client {client_code}"
                if matter_ref:
                    cashbook_desc += f", Matter {matter_ref}"

                txn_ledger = self.reserve_next_transaction_id()
                txn_cashbook = self.reserve_next_transaction_id()
                txn_office = self.reserve_next_transaction_id()

                cursor.execute("""
                    INSERT INTO ledger_transactions
                    (transaction_id, client_id, transaction_date, amount, transaction_type, reference,
                     source, description, linked_cashbook_id, created_by)
                    VALUES (?, ?, ?, ?, 'Payment', ?, ?, ?, ?, ?)
                """, (txn_ledger, client_id, transaction_date, str(amount), reference, source,
                      ledger_desc, None, created_by))
                ledger_id = cursor.lastrowid

                cursor.execute("""
                    INSERT INTO cashbook_transactions
                    (transaction_id, transaction_date, amount, transaction_type, reference, source,
                     description, status, linked_ledger_id, created_by)
                    VALUES (?, ?, ?, 'Payment', ?, ?, ?, 'Cleared', ?, ?)
                """, (txn_cashbook, transaction_date, str(amount), reference, source,
                      cashbook_desc, ledger_id, created_by))
                cashbook_id = cursor.lastrowid

                cursor.execute(
                    "UPDATE ledger_transactions SET linked_cashbook_id = ? WHERE id = ?",
                    (cashbook_id, ledger_id),
                )

                cursor.execute("""
                    INSERT INTO office_fee_transfers
                    (transaction_id, transaction_date, amount, client_id, ledger_transaction_id,
                     reference, description, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (txn_office, transaction_date, str(amount), client_id, ledger_id,
                      reference, ledger_desc, created_by))
                transfer_id = cursor.lastrowid

                conn.commit()
                try:
                    self._log_audit('ledger_transactions', ledger_id, 'INSERT', None,
                                     {'transaction_id': txn_ledger, 'client_id': client_id,
                                      'amount': str(amount), 'transaction_type': 'Payment',
                                      'reference': reference, 'office_transfer': True})
                    self._log_audit('cashbook_transactions', cashbook_id, 'INSERT', None,
                                     {'transaction_id': txn_cashbook, 'amount': str(amount),
                                      'reference': reference, 'status': 'Cleared',
                                      'office_transfer': True})
                    self._log_audit('office_fee_transfers', transfer_id, 'INSERT', None,
                                     {'transaction_id': txn_office, 'amount': str(amount),
                                      'client_id': client_id, 'ledger_transaction_id': ledger_id})
                except sqlite3.OperationalError:
                    pass
                return {
                    'ledger_id': ledger_id,
                    'cashbook_id': cashbook_id,
                    'office_fee_transfer_id': transfer_id,
                    'ledger_transaction_id': txn_ledger,
                    'client_code': client_code,
                    'matter_reference': matter_ref,
                }
            except ValueError:
                conn.rollback()
                raise
            except sqlite3.IntegrityError:
                conn.rollback()
                raise
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('create_transfer_fee_to_office', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")

    def create_office_fee_transfer(self, transaction_date: str, amount: Decimal,
                                   client_id: int, ledger_transaction_id: int,
                                   reference: str, description: str = None,
                                   created_by: str = 'System') -> int:
        """Record a fee transfer from client matter to office account (ledger Payment already created)."""
        self._ensure_month_unlocked(transaction_date)
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                txn_id = self.reserve_next_transaction_id()
                cursor.execute("""
                    INSERT INTO office_fee_transfers
                    (transaction_id, transaction_date, amount, client_id, ledger_transaction_id, reference, description, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (txn_id, transaction_date, str(amount), client_id, ledger_transaction_id, reference, description, created_by))
                transfer_id = cursor.lastrowid
                conn.commit()
                try:
                    self._log_audit('office_fee_transfers', transfer_id, 'INSERT', None,
                                   {'transaction_id': txn_id, 'amount': str(amount), 'client_id': client_id})
                except sqlite3.OperationalError:
                    pass
                return transfer_id
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('create_office_fee_transfer', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")
    
    def get_office_fee_transfers(self, start_date: str = None, end_date: str = None,
                                 created_by: str = None) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        query = """
            SELECT oft.*, c.client_code, c.client_name
            FROM office_fee_transfers oft
            JOIN clients c ON oft.client_id = c.id
            WHERE COALESCE(oft.is_deleted, 0) = 0
        """
        params = []
        if start_date:
            query += " AND oft.transaction_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND oft.transaction_date <= ?"
            params.append(end_date)
        if created_by:
            query += " AND oft.created_by = ?"
            params.append(created_by)
        query += " ORDER BY oft.transaction_date DESC, oft.id DESC"
        cursor.execute(query, params)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def reverse_ledger_transaction(
        self,
        ledger_id: int,
        performed_by: str,
        reason: str = None,
        reversal_transaction_date: str = None,
    ) -> Dict:
        """
        Create reversing entries for a ledger transaction. Admin-only operation.
        - Original transaction marked REVERSED (immutable - no edit to amounts)
        - New compensating transaction created with opposite type (chain allowed: reversal rows may be reversed)
        - Cashbook and office_fee_transfers also reversed if linked
        - Reversal is never blocked by projected ledger/cashbook agreement checks; mismatches are logged only.
        - Rows already marked REVERSED cannot be reversed again (use the active leg in the chain)
        """
        if not reason or not reason.strip():
            raise ValueError("Reversal reason is mandatory.")
        reverse_date = (reversal_transaction_date or '').strip() or datetime.now().strftime('%Y-%m-%d')
        self._ensure_month_unlocked(reverse_date)

        # Phase 1: Read original data to determine what IDs we need
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM ledger_transactions WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
                (ledger_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Ledger transaction not found.")
            original = dict(row)
            if (original.get('reversal_status') or 'ACTIVE') == 'REVERSED':
                raise ValueError("This transaction has already been reversed.")
            original_cashbook_id = original.get('linked_cashbook_id')
            has_cashbook = False
            cb_data = None
            if original_cashbook_id:
                cursor.execute(
                    "SELECT * FROM cashbook_transactions WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
                    (original_cashbook_id,),
                )
                cb_row = cursor.fetchone()
                if cb_row:
                    has_cashbook = True
                    cb_data = dict(cb_row)
            cursor.execute(
                "SELECT * FROM office_fee_transfers "
                "WHERE ledger_transaction_id = ? AND COALESCE(is_deleted, 0) = 0",
                (ledger_id,),
            )
            fee_rows = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

        # Phase 2: Pre-reserve all transaction IDs BEFORE starting the write transaction
        # This avoids deadlock from nested connections
        txn_ledger = self.reserve_next_transaction_id()
        txn_cb = self.reserve_next_transaction_id() if has_cashbook else None
        txn_fts = [self.reserve_next_transaction_id() for _ in fee_rows]

        orig_depth = int(original.get('reversal_depth') or 0)
        new_depth = orig_depth + 1
        cid = int(original['client_id'])
        pre_led = self.get_cleared_client_balance(cid)
        pre_cb = self.get_client_cashbook_net_balance(cid)
        logger.info(
            'Reversal pre-balances client_id=%s ledger_net=%s cashbook_net=%s',
            cid, pre_led, pre_cb,
        )

        # Phase 3: Execute all writes in a single connection
        reverse_type = 'Receipt' if original['transaction_type'] in ('Payment', 'Transfer') else 'Payment'
        reverse_ref = f"REV-{ledger_id}"
        orig_ref = (original.get('reference') or '').strip() or f"ledger#{ledger_id}"
        orig_desc = (original.get('description') or '').strip()
        if orig_desc:
            reverse_desc = f"REVERSAL OF: {orig_ref} — {orig_desc}. Reason: {reason}"
        else:
            reverse_desc = f"REVERSAL OF: {orig_ref}. Reason: {reason}"

        logger.info(
            'Reversing ledger row id=%s ref=%r type=%r reversal_status=%r',
            ledger_id, orig_ref, original.get('transaction_type'), original.get('reversal_status'),
        )
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                # Insert reversal ledger entry
                cursor.execute("""
                    INSERT INTO ledger_transactions
                    (transaction_id, client_id, transaction_date, amount, transaction_type, reference,
                     source, description, linked_cashbook_id, created_by, reversal_of,
                     parent_transaction_id, reversal_of_transaction_id, reversal_status, reversal_depth)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                """, (txn_ledger, original['client_id'], reverse_date, str(original['amount']), reverse_type,
                      reverse_ref, original['source'], reverse_desc, None, performed_by, ledger_id,
                      ledger_id, ledger_id, new_depth))
                reversal_ledger_id = cursor.lastrowid
                logger.info('Created reversal ledger row id=%s txn_id=%r', reversal_ledger_id, txn_ledger)

                # Mark original as REVERSED
                cursor.execute("""
                    UPDATE ledger_transactions
                    SET reversal_status = 'REVERSED', reversed_at = CURRENT_TIMESTAMP,
                        reversed_by = ?, reversal_reason = ?
                    WHERE id = ?
                """, (performed_by, reason, ledger_id))

                # Reverse linked cashbook if exists
                reversal_cashbook_id = None
                if has_cashbook and cb_data:
                    cb_type = 'Receipt' if cb_data['transaction_type'] == 'Payment' else 'Payment'
                    cb_rev_of = cb_data['id']
                    cursor.execute("""
                        INSERT INTO cashbook_transactions
                        (transaction_id, transaction_date, amount, transaction_type, reference, source,
                         description, status, linked_ledger_id, created_by, reversal_of,
                         parent_transaction_id, reversal_of_transaction_id, reversal_status, reversal_depth)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'Cleared', ?, ?, ?, ?, ?, 'ACTIVE', ?)
                    """, (txn_cb, reverse_date, str(cb_data['amount']), cb_type, reverse_ref, cb_data['source'],
                          f"REVERSAL OF: cashbook #{cb_data['id']} ({cb_data.get('reference') or ''}). Reason: {reason}", reversal_ledger_id, performed_by, cb_rev_of,
                          cb_rev_of, cb_rev_of, new_depth))
                    reversal_cashbook_id = cursor.lastrowid
                    cursor.execute("UPDATE ledger_transactions SET linked_cashbook_id = ? WHERE id = ?",
                                   (reversal_cashbook_id, reversal_ledger_id))
                    cursor.execute("""
                        UPDATE cashbook_transactions
                        SET reversal_status = 'REVERSED', reversed_at = CURRENT_TIMESTAMP, reversed_by = ?
                        WHERE id = ?
                    """, (performed_by, original_cashbook_id))

                # Reverse any linked fee transfers
                for i, ft in enumerate(fee_rows):
                    cursor.execute("""
                        INSERT INTO office_fee_transfers
                        (transaction_id, transaction_date, amount, client_id, ledger_transaction_id, reference, description, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (txn_fts[i], reverse_date, str(-Decimal(str(ft['amount']))), ft['client_id'],
                          reversal_ledger_id, f"REV-FT-{ft['id']}", f"REVERSAL OF: fee transfer #{ft['id']} ({ft.get('reference') or ''}). Reason: {reason}", performed_by))

                post_led = self._cursor_sum_ledger_client(cursor, cid)
                post_cb = self._cursor_sum_cashbook_client(cursor, cid)
                logger.info(
                    'Reversal post-balances (client scope) client_id=%s ledger_net=%s cashbook_net=%s',
                    cid, post_led, post_cb,
                )
                tot_led = self._cursor_sum_all_clients_ledger(cursor, None)
                tot_cb = self._cursor_sum_all_client_cashbook(cursor, None)
                logger.info(
                    'Reversal post-balances (system totals) ledger_net=%s cashbook_net=%s',
                    tot_led, tot_cb,
                )
                mismatch_client = abs(post_led - post_cb) > Decimal('0.02')
                mismatch_total = abs(tot_led - tot_cb) > Decimal('0.02')
                if mismatch_client:
                    logger.warning(
                        'Ledger/Cashbook mismatch after reversal (client %s): ledger=%s cashbook=%s — posted anyway',
                        cid, post_led, post_cb,
                    )
                if mismatch_total:
                    logger.warning(
                        'Ledger/Cashbook mismatch after reversal (all clients): ledger=%s cashbook=%s — posted anyway',
                        tot_led, tot_cb,
                    )

                conn.commit()
                return {
                    'original_ledger_id': ledger_id,
                    'reversal_ledger_id': reversal_ledger_id,
                    'reversal_ledger_txn_id': txn_ledger,
                    'original_cashbook_id': original_cashbook_id,
                    'reversal_cashbook_id': reversal_cashbook_id,
                    'client_id': original['client_id'],
                    'amount': original['amount'],
                    'original_type': original['transaction_type'],
                    'reversal_type': reverse_type,
                    'reason': reason,
                    'mismatch_client': mismatch_client,
                    'mismatch_total': mismatch_total,
                    'post_ledger': post_led,
                    'post_cashbook': post_cb,
                    'total_ledger': tot_led,
                    'total_cashbook': tot_cb,
                }
            except ValueError:
                conn.rollback()
                raise
            except sqlite3.IntegrityError as e:
                conn.rollback()
                raise ValueError(f"Reversal could not be saved (database constraint): {e}") from e
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('reverse_ledger_transaction', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")
    
    def get_office_balance(self, as_of_date: str = None) -> Decimal:
        """
        Office balance from CLEARED, non-REVERSED transactions only.
        
        Excludes:
        - Pending cheques (not yet cleared)
        - REVERSED transactions (original entries that have been reversed)
        
        = fee transfers + (legacy unlinked cashbook CLEARED) + (office_cashbook CLEARED)
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Fee transfers (office income) - always count as cleared
        # Note: office_fee_transfers don't currently support reversal - they link to ledger reversals
        q1 = "SELECT COALESCE(SUM(amount), 0) FROM office_fee_transfers WHERE COALESCE(is_deleted, 0) = 0"
        params1 = []
        if as_of_date:
            q1 += " AND transaction_date <= ?"
            params1.append(as_of_date)
        cursor.execute(q1, params1)
        fee_total = Decimal(str(cursor.fetchone()[0] or 0))
        
        # Legacy unlinked cashbook (CLEARED only, exclude REVERSED)
        q2 = """
            SELECT COALESCE(SUM(
                CASE 
                    WHEN transaction_type = 'Receipt' THEN amount
                    WHEN transaction_type = 'Payment' THEN -amount
                    ELSE 0
                END
            ), 0)
            FROM cashbook_transactions
            WHERE linked_ledger_id IS NULL 
              AND status = 'Cleared'
              AND COALESCE(is_deleted, 0) = 0
        """ + _CASHBOOK_BALANCE_EFFECTIVE_SQL
        params2 = []
        if as_of_date:
            q2 += " AND transaction_date <= ?"
            params2.append(as_of_date)
        cursor.execute(q2, params2)
        cashbook_office = Decimal(str(cursor.fetchone()[0] or 0))

        # office_cashbook (CLEARED only - pending cheques not counted)
        q3 = """
            SELECT COALESCE(SUM(
                CASE
                    WHEN transaction_type = 'Receipt' THEN amount
                    WHEN transaction_type = 'Payment' THEN -amount
                    ELSE 0
                END
            ), 0)
            FROM office_cashbook
            WHERE status = 'Cleared'
              AND COALESCE(is_deleted, 0) = 0
        """
        params3 = []
        if as_of_date:
            q3 += " AND transaction_date <= ?"
            params3.append(as_of_date)
        cursor.execute(q3, params3)
        office_cb_net = Decimal(str(cursor.fetchone()[0] or 0))

        conn.close()
        return fee_total + cashbook_office + office_cb_net

    def _get_office_cashbook_rows(self, start_date: str = None, end_date: str = None,
                                  created_by: str = None) -> List[Dict]:
        """Return office_cashbook rows for office transactions list."""
        conn = self.get_connection()
        cursor = conn.cursor()
        query = "SELECT * FROM office_cashbook WHERE COALESCE(is_deleted, 0) = 0"
        params = []
        if start_date:
            query += " AND transaction_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND transaction_date <= ?"
            params.append(end_date)
        if created_by:
            query += " AND created_by = ?"
            params.append(created_by)
        query += " ORDER BY transaction_date DESC, id DESC"
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def get_office_transactions(self, start_date: str = None, end_date: str = None,
                                created_by: str = None) -> List[Dict]:
        """Combined list: fee transfers (as Office Income) + unlinked cashbook entries."""
        fee_transfers = self.get_office_fee_transfers(start_date, end_date, created_by)
        office_rows = []
        for ft in fee_transfers:
            office_rows.append({
                'id': f"ft-{ft['id']}",
                'transaction_id': ft.get('transaction_id'),
                'transaction_date': ft['transaction_date'],
                'amount': ft['amount'],
                'transaction_type': 'Fee Transfer',
                'reference': ft['reference'],
                'source': 'N/A',
                'description': ft.get('description') or f"Fee from {ft.get('client_code', '')} - {ft.get('client_name', '')}",
                'status': 'Cleared',
                'client_code': ft.get('client_code'),
                'client_name': ft.get('client_name'),
                'is_fee_transfer': True,
                'created_by': ft.get('created_by', 'System'),
            })
        
        # office_cashbook (dedicated office receipts/expenses - never in client cashbook)
        oc_rows = self._get_office_cashbook_rows(start_date, end_date, created_by)
        for oc in oc_rows:
            office_rows.append({
                'id': f"oc-{oc['id']}",
                'transaction_id': oc.get('transaction_id'),
                'transaction_date': oc['transaction_date'],
                'amount': oc['amount'],
                'transaction_type': oc['transaction_type'],
                'reference': oc['reference'],
                'source': oc['source'],
                'description': oc.get('description'),
                'status': oc['status'],
                'client_code': None,
                'client_name': None,
                'is_fee_transfer': False,
                'office_cashbook_id': oc['id'],
                'created_by': oc.get('created_by', 'System'),
            })

        # Legacy unlinked cashbook (kept for backward compatibility)
        all_cashbook = self.get_all_cashbook_transactions(start_date, end_date, created_by=created_by)
        for cb in all_cashbook:
            if cb.get('linked_ledger_id') is None:
                office_rows.append({
                    'id': f"cb-{cb['id']}",
                    'transaction_id': cb.get('transaction_id'),
                    'transaction_date': cb['transaction_date'],
                    'amount': cb['amount'],
                    'transaction_type': cb['transaction_type'],
                    'reference': cb['reference'],
                    'source': cb['source'],
                    'description': cb.get('description'),
                    'status': cb['status'],
                    'client_code': None,
                    'client_name': None,
                    'is_fee_transfer': False,
                    'cashbook_id': cb['id'],
                    'created_by': cb.get('created_by', 'System'),
                })
        
        # Sort by date desc, id desc
        office_rows.sort(key=lambda x: (x['transaction_date'], x['id']), reverse=True)
        return office_rows
    
    def get_office_income_total(self, start_date: str = None, end_date: str = None) -> Decimal:
        """
        Total office income from CLEARED, non-REVERSED transactions only.
        
        Excludes:
        - Pending cheques (not yet cleared)
        - REVERSED transactions (original entries that have been reversed)
        
        = fee transfers + legacy unlinked cashbook Receipts (CLEARED) + office_cashbook Receipts (CLEARED)
        
        Excludes both REVERSED originals AND reversal entries for net zero impact.
        """
        fee_transfers = self.get_office_fee_transfers(start_date, end_date)
        fee_total = sum(Decimal(str(ft['amount'])) for ft in fee_transfers)
        all_cashbook = self.get_all_cashbook_transactions(start_date, end_date)
        receipt_total = sum(Decimal(str(cb['amount'])) for cb in all_cashbook
                          if cb.get('linked_ledger_id') is None
                          and cb['transaction_type'] == 'Receipt'
                          and cb['status'] == 'Cleared'
                          and (cb.get('reversal_status') or 'ACTIVE') != 'REVERSED'
                          and (cb.get('reversal_depth') or 0) % 2 == 0)
        oc_rows = self._get_office_cashbook_rows(start_date, end_date)
        oc_receipts = sum(Decimal(str(oc['amount'])) for oc in oc_rows
                         if oc['transaction_type'] == 'Receipt' and oc['status'] == 'Cleared')
        return fee_total + receipt_total + oc_receipts

    def get_office_expenses_total(self, start_date: str = None, end_date: str = None) -> Decimal:
        """
        Total office expenses from CLEARED, non-REVERSED transactions only.
        
        Excludes:
        - Pending cheques (not yet cleared)
        - REVERSED transactions (original entries that have been reversed)
        - REVERSAL entries (compensating entries for audit trail only)
        
        = legacy unlinked cashbook Payments (CLEARED) + office_cashbook Payments (CLEARED)
        
        Excludes both REVERSED originals AND reversal entries for net zero impact.
        """
        all_cashbook = self.get_all_cashbook_transactions(start_date, end_date)
        cb_total = sum(Decimal(str(cb['amount'])) for cb in all_cashbook
                      if cb.get('linked_ledger_id') is None
                      and cb['transaction_type'] == 'Payment'
                      and cb['status'] == 'Cleared'
                      and (cb.get('reversal_status') or 'ACTIVE') != 'REVERSED'
                      and (cb.get('reversal_depth') or 0) % 2 == 0)
        oc_rows = self._get_office_cashbook_rows(start_date, end_date)
        oc_payments = sum(Decimal(str(oc['amount'])) for oc in oc_rows
                         if oc['transaction_type'] == 'Payment' and oc['status'] == 'Cleared')
        return cb_total + oc_payments
    
    def get_office_cashbook_net_for_reconciliation(self, as_of_date: str = None) -> Decimal:
        """
        Net movement from office_cashbook (CLEARED only) for reconciliation.
        Pending cheques do NOT count in reconciliation totals.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        query = """
            SELECT COALESCE(SUM(
                CASE
                    WHEN transaction_type = 'Receipt' THEN amount
                    WHEN transaction_type = 'Payment' THEN -amount
                    ELSE 0
                END
            ), 0)
            FROM office_cashbook
            WHERE status = 'Cleared'
              AND COALESCE(is_deleted, 0) = 0
        """
        params = []
        if as_of_date:
            query += " AND transaction_date <= ?"
            params.append(as_of_date)
        cursor.execute(query, params)
        result = Decimal(str(cursor.fetchone()[0] or 0))
        conn.close()
        return result

    def get_current_reconciliations(self) -> List[Dict]:
        """Return the active (current) reconciliation for each month — operational view."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, reconciliation_date, reconciliation_month, reconciliation_year,
                   ledger_total, cashbook_total, bank_balance, variance, notes,
                   created_date, reconciled_by, locked, locked_by_user, locked_timestamp,
                   version, is_current
            FROM reconciliations
            WHERE is_current = 1 AND COALESCE(is_deleted, 0) = 0
            ORDER BY reconciliation_year DESC, reconciliation_month DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_reconciliations(self) -> List[Dict]:
        """Return full version history (current and archived), newest first."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, reconciliation_date, reconciliation_month, reconciliation_year,
                   ledger_total, cashbook_total, bank_balance, variance, notes,
                   created_date, reconciled_by, locked, locked_by_user, locked_timestamp,
                   version, is_current
            FROM reconciliations
            WHERE COALESCE(is_deleted, 0) = 0
            ORDER BY reconciliation_year DESC, reconciliation_month DESC,
                     version DESC, id DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_most_recent_reconciliation_date(self) -> Optional[str]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT reconciliation_date FROM reconciliations "
            "WHERE COALESCE(is_deleted, 0) = 0 "
            "ORDER BY reconciliation_date DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        return row['reconciliation_date'] if row else None

    def get_closed_matters_count(self) -> int:
        """Return count of clients with matter_status = 'CLOSED'."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as n FROM clients WHERE is_active = 1 AND matter_status = 'CLOSED'"
        )
        row = cursor.fetchone()
        conn.close()
        return row['n'] if row else 0

    def get_pending_cheques_older_than_days(self, days: int) -> List[Dict]:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM cashbook_transactions
            WHERE source = 'Cheque' AND status = 'Pending' AND transaction_date < ?
              AND COALESCE(is_deleted, 0) = 0
        """, (cutoff,))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def verify_database_integrity(self) -> Tuple[bool, str]:
        """Run PRAGMA integrity_check. Returns (ok, message)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        row = cursor.fetchone()
        conn.close()
        result = row[0] if row else 'unknown'
        return (result == 'ok', str(result))

    def verify_ledger_consistency(self) -> Optional[str]:
        """Verify no negative balances and basic consistency. Returns error message or None if OK."""
        clients = self.get_all_clients()
        for c in clients:
            bal = self.get_client_balance(c['id'])
            if bal < Decimal('0'):
                return f"Negative client balance: {c.get('client_code')} = £{bal}"
        office = self.get_office_balance()
        if office < Decimal('0'):
            return f"Negative office balance: £{office}"
        return None

    def get_total_ledger_balance(self, as_of_date: str = None) -> Decimal:
        """
        Total of all client matter ledger nets (cleared cheque filter + reversal_depth parity).
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            return self._cursor_sum_all_clients_ledger(cursor, as_of_date)
        finally:
            conn.close()
    
    def _reverse_ledger_transaction(self, ledger_id: int, reason: str = None):
        # Phase 1: Read original data
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM ledger_transactions WHERE id = ?", (ledger_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("Ledger transaction not found")
            original = dict(row)
        finally:
            conn.close()

        # Phase 2: Pre-reserve transaction ID
        txn_id = self.reserve_next_transaction_id()

        # Phase 3: Execute write in a single transaction
        reversal_type = 'Payment' if original['transaction_type'] == 'Receipt' else 'Receipt'
        reversal_ref = f"REV-{original['reference']}"
        last_err = None
        for attempt in range(DB_WRITE_RETRIES):
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO ledger_transactions
                    (transaction_id, client_id, transaction_date, amount, transaction_type, reference, source, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (txn_id, original['client_id'], datetime.now().strftime('%Y-%m-%d'),
                      original['amount'], reversal_type, reversal_ref, original['source'],
                      f"Reversal of {original['reference']}. Reason: {reason or 'Declined'}"))
                conn.commit()
                try:
                    self._log_audit('ledger_transactions', ledger_id, 'REVERSE', original,
                                   {'reason': reason}, reason=reason)
                except sqlite3.OperationalError:
                    pass
                return
            except sqlite3.OperationalError as e:
                conn.rollback()
                last_err = e
                if 'locked' in str(e).lower() or 'busy' in str(e).lower():
                    _log_db_retry('_reverse_ledger_transaction', attempt + 1, e)
                    time.sleep(DB_WRITE_RETRY_DELAY)
                else:
                    raise ValueError(f"Database error: {e}")
            finally:
                conn.close()
        raise ValueError(f"Database busy after {DB_WRITE_RETRIES} retries: {last_err}")
    
    def _log_audit(self, table_name: str, record_id: int, action: str,
                  old_values: Optional[Dict], new_values: Dict, reason: str = None):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO audit_trail
            (table_name, record_id, action, old_values, new_values, reason)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (table_name, record_id, action,
              json.dumps(old_values) if old_values else None,
              json.dumps(new_values) if new_values else None,
              reason))
        conn.commit()
        conn.close()

    def insert_audit_log(self, username: str, role: str, action: str, module: str,
                         record_id: str = None, details: str = None):
        """Insert immutable audit log entry. No UI can edit or delete."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO audit_log (username, role, action, module, record_id, details)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (username, role, action, module, record_id, details))
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
        finally:
            conn.close()

    def get_audit_log_entries(self, username: str = None, module: str = None,
                              date_from: str = None, date_to: str = None,
                              limit: int = 500) -> List[Dict]:
        """Get audit log entries, newest first. Admin-only."""
        conn = self.get_connection()
        cursor = conn.cursor()
        query = "SELECT * FROM audit_log WHERE COALESCE(is_deleted, 0) = 0"
        params = []
        if username:
            query += " AND username = ?"
            params.append(username)
        if module:
            query += " AND module = ?"
            params.append(module)
        if date_from:
            query += " AND DATE(timestamp) >= ?"
            params.append(date_from)
        if date_to:
            query += " AND DATE(timestamp) <= ?"
            params.append(date_to)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def get_audit_usernames(self) -> List[str]:
        """Get distinct usernames from audit_log for filter dropdown."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT username FROM audit_log WHERE COALESCE(is_deleted, 0) = 0 ORDER BY username"
        )
        usernames = [row[0] for row in cursor.fetchall()]
        conn.close()
        return usernames

    def get_audit_modules(self) -> List[str]:
        """Get distinct modules from audit_log for filter dropdown."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT module FROM audit_log WHERE COALESCE(is_deleted, 0) = 0 ORDER BY module"
        )
        modules = [row[0] for row in cursor.fetchall()]
        conn.close()
        return modules
    
    def get_config(self, key: str, default: str = None) -> str:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row['value'] if row else default

    def set_config(self, key: str, value: str, description: str = None):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_config (key, value, description, updated_date)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_date = CURRENT_TIMESTAMP
        """, (key, str(value), description))
        conn.commit()
        conn.close()
