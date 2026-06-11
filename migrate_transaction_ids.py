"""
Migration script: Populate transaction_id for existing records.
Run once if upgrading from a version without transaction IDs.
Normally runs automatically on app startup via database init.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import Database

if __name__ == '__main__':
    db = Database()
    print("Migration complete. Transaction IDs have been assigned to existing records.")
