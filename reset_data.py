"""
Standalone hard reset for the Solicitor Ledger database.

Wipes ALL financial and related data while keeping the schema intact, then
re-seeds default users (admin / staff). Does not run via HTTP.

This project uses sqlite3 via database.Database — not SQLAlchemy ORM models.

Usage:
    python reset_data.py

Safety:
    Set SOLICITOR_NO_RESET to any non-empty value to block execution.
    Always take a backup first — this is irreversible.
"""

import sys
import os

# Project root on path (same directory as this script)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _print_counts(db) -> int:
    """Return total row count across data tables that reset_database clears."""
    conn = db.get_connection()
    cursor = conn.cursor()
    tables = [
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
    print('\n  Current data counts (before reset):')
    total = 0
    for t in tables:
        try:
            cursor.execute(f'SELECT COUNT(*) FROM {t}')
            n = int(cursor.fetchone()[0])
            total += n
            if n > 0:
                print(f'    {t:35s} {n:>8,} rows')
        except Exception:
            print(f'    {t:35s} (unreadable)')
    try:
        cursor.execute('SELECT COUNT(*) FROM users')
        nu = int(cursor.fetchone()[0])
        print(f'    {"users":35s} {nu:>8,} rows (will be replaced with defaults)')
    except Exception:
        pass
    conn.close()
    return total


def main() -> int:
    if os.environ.get('SOLICITOR_NO_RESET', ''):
        print('\n  ERROR: Reset is blocked by SOLICITOR_NO_RESET.\n')
        return 1

    # Shared app instance and Database singleton used by the Flask app
    from app import app, db

    with app.app_context():
        total = _print_counts(db)

        print('\n  WARNING: This permanently deletes ALL ledger, cashbook, office,')
        print('  reconciliation, audit, client, and user data, then re-creates default users.')
        print('  Schema is preserved. This cannot be undone.\n')

        print('\n  Deleting all financial and related data...')
        try:
            db.reset_database(confirm=True)
        except RuntimeError as e:
            print(f'\n  ERROR: {e}\n')
            return 1

        print('\n  Verifying empty state...')
        conn = db.get_connection()
        cur = conn.cursor()
        checks = [
            ('ledger_transactions', 0),
            ('cashbook_transactions', 0),
            ('office_cashbook', 0),
            ('office_fee_transfers', 0),
            ('reconciliations', 0),
            ('audit_trail', 0),
            ('audit_log', 0),
            ('clients', 0),
            ('cheque_status_log', 0),
            ('month_locks', 0),
            ('reset_tokens', 0),
            ('reconciliation_bank_session', 0),
        ]
        for table, expected in checks:
            cur.execute(f'SELECT COUNT(*) FROM {table}')
            n = int(cur.fetchone()[0])
            if n != expected:
                conn.close()
                print(
                    f'\n  ERROR: Reset verification failed: {table} has {n} rows, expected {expected}.\n'
                )
                return 1
        cur.execute('SELECT COUNT(*) FROM users')
        nu = int(cur.fetchone()[0])
        conn.close()
        if nu < 1:
            print('\n  ERROR: Reset verification failed: no users after re-seed.\n')
            return 1

        print('\n  ✅ SYSTEM FULLY RESET')
        print('  Default users: admin / staff (change passwords in production)')
        print('  All balances: £0.00. Ready for fresh use.\n')
        return 0


if __name__ == '__main__':
    sys.exit(main())
