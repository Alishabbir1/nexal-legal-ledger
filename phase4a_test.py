"""
Nexal Legal – Phase 4A: Test Suite
====================================
Tests for:
  1. Platform database (firms table, workspaces table)
  2. provision_firm() function
  3. Database routing (db_router)
  4. Tenant isolation verification
  5. Role foundation (expanded roles, new columns)
  6. Existing ledger functionality (backwards compatibility)

Run with:
    python phase4a_test.py
    # or
    pytest phase4a_test.py -v
"""

import os
import sys
import shutil
import sqlite3
import tempfile
import unittest
import logging

logging.basicConfig(level=logging.WARNING)

# ---------------------------------------------------------------------------
# Test base – isolated temp directory per test run
# ---------------------------------------------------------------------------

class Phase4ATestBase(unittest.TestCase):
    """Base class that creates a fresh temp directory for each test."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexal_phase4a_test_")
        os.environ["NEXAL_DATA_DIR"] = self.test_dir
        # Clear db_router cache between tests
        try:
            from db_router import clear_db_cache
            clear_db_cache()
        except ImportError:
            pass

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        if "NEXAL_DATA_DIR" in os.environ:
            del os.environ["NEXAL_DATA_DIR"]
        try:
            from db_router import clear_db_cache
            clear_db_cache()
        except ImportError:
            pass


# ===========================================================================
# TEST 1: PlatformDB
# ===========================================================================

class TestPlatformDB(Phase4ATestBase):

    def test_platform_db_creates_schema(self):
        """PlatformDB should create firms and workspaces tables."""
        from platform_db import PlatformDB
        pdb = PlatformDB(db_path=os.path.join(self.test_dir, "platform.db"))
        conn = sqlite3.connect(pdb.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        self.assertIn("firms", tables, "firms table must exist")
        self.assertIn("workspaces", tables, "workspaces table must exist")

    def test_create_and_get_firm(self):
        """Should create a firm and retrieve it by firm_id."""
        from platform_db import PlatformDB
        pdb = PlatformDB(db_path=os.path.join(self.test_dir, "platform.db"))
        firm = pdb.create_firm("FIRM001", "Smith & Partners", "/data/firms/FIRM001/db")
        self.assertEqual(firm["firm_id"], "FIRM001")
        self.assertEqual(firm["firm_name"], "Smith & Partners")
        self.assertEqual(firm["status"], "active")

        retrieved = pdb.get_firm("FIRM001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["firm_id"], "FIRM001")

    def test_firm_not_found_returns_none(self):
        from platform_db import PlatformDB
        pdb = PlatformDB(db_path=os.path.join(self.test_dir, "platform.db"))
        self.assertIsNone(pdb.get_firm("NONEXISTENT"))

    def test_list_firms(self):
        from platform_db import PlatformDB
        pdb = PlatformDB(db_path=os.path.join(self.test_dir, "platform.db"))
        pdb.create_firm("FIRM001", "Alpha Law", "/path/a")
        pdb.create_firm("FIRM002", "Beta Solicitors", "/path/b")
        firms = pdb.list_firms()
        self.assertEqual(len(firms), 2)

    def test_list_firms_by_status(self):
        from platform_db import PlatformDB
        pdb = PlatformDB(db_path=os.path.join(self.test_dir, "platform.db"))
        pdb.create_firm("FIRM001", "Alpha Law", "/path/a")
        pdb.create_firm("FIRM002", "Beta Solicitors", "/path/b")
        pdb.update_firm_status("FIRM002", "suspended")
        active = pdb.list_firms(status="active")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["firm_id"], "FIRM001")

    def test_create_workspace(self):
        from platform_db import PlatformDB
        pdb = PlatformDB(db_path=os.path.join(self.test_dir, "platform.db"))
        pdb.create_firm("FIRM001", "Alpha Law", "/path/a")
        ws = pdb.create_workspace("WS-FIRM001", "FIRM001", "Alpha Workspace", "/path/a")
        self.assertEqual(ws["workspace_id"], "WS-FIRM001")
        self.assertEqual(ws["firm_id"], "FIRM001")

    def test_firms_table_fields(self):
        """Verify all required fields exist in firms table."""
        from platform_db import PlatformDB
        pdb = PlatformDB(db_path=os.path.join(self.test_dir, "platform.db"))
        conn = sqlite3.connect(pdb.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(firms)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        for required in ["firm_id", "firm_name", "status", "db_path",
                         "created_at", "updated_at"]:
            self.assertIn(required, cols, f"Missing column: {required}")

    def test_workspaces_table_fields(self):
        """Verify all required fields exist in workspaces table."""
        from platform_db import PlatformDB
        pdb = PlatformDB(db_path=os.path.join(self.test_dir, "platform.db"))
        conn = sqlite3.connect(pdb.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(workspaces)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        for required in ["workspace_id", "firm_id", "workspace_name",
                         "db_path", "status", "created_at", "updated_at"]:
            self.assertIn(required, cols, f"Missing column: {required}")


# ===========================================================================
# TEST 2: Provisioning
# ===========================================================================

class TestProvisioning(Phase4ATestBase):

    def test_provision_firm_creates_db(self):
        """provision_firm should create a DB file for the firm."""
        from provisioning import provision_firm
        result = provision_firm("FIRM001", "Smith & Partners LLP")
        self.assertEqual(result["firm_id"], "FIRM001")
        self.assertEqual(result["workspace_id"], "WS-FIRM001")
        self.assertTrue(os.path.exists(result["db_path"]),
                        "Firm DB file must exist after provisioning")

    def test_provision_creates_workspace(self):
        from provisioning import provision_firm
        from platform_db import PlatformDB
        provision_firm("FIRM002", "Jones & Co")
        pdb = PlatformDB()
        ws = pdb.get_workspace_for_firm("FIRM002")
        self.assertIsNotNone(ws)
        self.assertEqual(ws["firm_id"], "FIRM002")

    def test_provision_registers_firm_in_platform_db(self):
        from provisioning import provision_firm
        from platform_db import PlatformDB
        provision_firm("FIRM003", "Williams Legal")
        pdb = PlatformDB()
        firm = pdb.get_firm("FIRM003")
        self.assertIsNotNone(firm)
        self.assertEqual(firm["firm_name"], "Williams Legal")

    def test_provision_duplicate_firm_raises_error(self):
        from provisioning import provision_firm
        provision_firm("FIRM001", "Smith & Partners LLP")
        with self.assertRaises(ValueError):
            provision_firm("FIRM001", "Smith Again")

    def test_provision_invalid_firm_id(self):
        from provisioning import provision_firm
        with self.assertRaises(ValueError):
            provision_firm("invalid id!", "Test Firm")

    def test_provision_multiple_firms_independent(self):
        """Three firms should each get their own independent DB."""
        from provisioning import provision_firm
        r1 = provision_firm("FIRM001", "Alpha Law")
        r2 = provision_firm("FIRM002", "Beta Solicitors")
        r3 = provision_firm("FIRM003", "Gamma Legal")
        paths = {r1["db_path"], r2["db_path"], r3["db_path"]}
        self.assertEqual(len(paths), 3,
                         "Each firm must have a unique database path")
        for path in paths:
            self.assertTrue(os.path.exists(path))

    def test_provision_returns_correct_structure(self):
        from provisioning import provision_firm
        result = provision_firm("FIRM001", "Test Firm Ltd")
        for key in ["firm_id", "firm_name", "workspace_id", "workspace_name",
                    "db_path", "status", "created_at"]:
            self.assertIn(key, result, f"Missing key in result: {key}")


# ===========================================================================
# TEST 3: Database Routing
# ===========================================================================

class TestDBRouter(Phase4ATestBase):

    def _provision(self, firm_id, firm_name):
        from provisioning import provision_firm
        return provision_firm(firm_id, firm_name)

    def test_get_db_for_firm_returns_database(self):
        from db_router import get_db_for_firm
        from database import Database
        self._provision("FIRM001", "Alpha Law")
        db = get_db_for_firm("FIRM001")
        self.assertIsInstance(db, Database)

    def test_get_db_for_nonexistent_firm_raises_error(self):
        from db_router import get_db_for_firm
        with self.assertRaises((ValueError, FileNotFoundError)):
            get_db_for_firm("NOFIRM")

    def test_get_db_for_workspace(self):
        from db_router import get_db_for_workspace
        from database import Database
        self._provision("FIRM001", "Alpha Law")
        db = get_db_for_workspace("WS-FIRM001")
        self.assertIsInstance(db, Database)

    def test_list_active_firm_ids(self):
        from db_router import list_active_firm_ids
        self._provision("FIRM001", "Alpha Law")
        self._provision("FIRM002", "Beta Solicitors")
        ids = list_active_firm_ids()
        self.assertIn("FIRM001", ids)
        self.assertIn("FIRM002", ids)

    def test_resolve_firm_db_path(self):
        from db_router import resolve_firm_db_path
        result = self._provision("FIRM001", "Alpha Law")
        resolved = resolve_firm_db_path("FIRM001")
        self.assertEqual(resolved, result["db_path"])


# ===========================================================================
# TEST 4: Tenant Isolation
# ===========================================================================

class TestTenantIsolation(Phase4ATestBase):

    def test_firms_use_different_databases(self):
        """Core isolation test: Firm A and Firm B must have separate DBs."""
        from provisioning import provision_firm
        from db_router import verify_isolation
        provision_firm("FIRM001", "Alpha Law")
        provision_firm("FIRM002", "Beta Solicitors")
        result = verify_isolation("FIRM001", "FIRM002")
        self.assertTrue(result["isolated"],
                        "Firm A and Firm B must use completely different DB files")

    def test_data_written_to_firm_a_not_visible_in_firm_b(self):
        """Data written to Firm A's DB must not appear in Firm B's DB."""
        from provisioning import provision_firm
        from db_router import get_db_for_firm

        provision_firm("FIRM001", "Alpha Law")
        provision_firm("FIRM002", "Beta Solicitors")

        db_a = get_db_for_firm("FIRM001")
        db_b = get_db_for_firm("FIRM002")

        # Add a client to Firm A
        db_a.add_client(
            client_code="CL001",
            client_name="Test Client Alpha",
            matter_reference="MAT001"
        )

        # Firm B should have no clients
        clients_b = db_b.get_all_clients()
        client_codes_b = [c["client_code"] for c in clients_b]
        self.assertNotIn("CL001", client_codes_b,
                         "Client from Firm A must not appear in Firm B")

    def test_three_firm_isolation(self):
        """All three test firms must have fully isolated databases."""
        from provisioning import provision_firm
        r1 = provision_firm("FIRM001", "Alpha Law")
        r2 = provision_firm("FIRM002", "Beta Solicitors")
        r3 = provision_firm("FIRM003", "Gamma Legal")
        paths = [
            os.path.realpath(r1["db_path"]),
            os.path.realpath(r2["db_path"]),
            os.path.realpath(r3["db_path"])
        ]
        self.assertEqual(len(set(paths)), 3,
                         "All three firms must have unique real DB paths")


# ===========================================================================
# TEST 5: Role Foundation
# ===========================================================================

class TestRoleFoundation(Phase4ATestBase):

    def test_migration_adds_columns_to_ledger_db(self):
        """Phase 4A migration should add firm_id, email, portal_user_id columns."""
        from provisioning import provision_firm
        from phase4a_migrate import migrate_db
        result = provision_firm("FIRM001", "Alpha Law")
        migrate_result = migrate_db(result["db_path"])
        self.assertEqual(migrate_result["status"], "ok",
                         f"Migration failed: {migrate_result['message']}")
        # Verify columns exist
        conn = sqlite3.connect(result["db_path"])
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        for col in ["firm_id", "email", "portal_user_id"]:
            self.assertIn(col, cols, f"Missing column '{col}' after migration")

    def test_existing_users_unaffected_by_migration(self):
        """Migration must not break existing users."""
        from provisioning import provision_firm
        from phase4a_migrate import migrate_db
        from database import Database
        result = provision_firm("FIRM001", "Alpha Law")
        db = Database(db_path=result["db_path"])
        db.create_user("testadmin", "TestPass123!", "admin")
        migrate_db(result["db_path"])
        user = db.get_user_by_username("testadmin")
        self.assertIsNotNone(user, "Existing user must survive migration")
        self.assertEqual(user["role"], "admin")

    def test_migration_idempotent(self):
        """Running migration twice should not cause errors."""
        from provisioning import provision_firm
        from phase4a_migrate import migrate_db
        result = provision_firm("FIRM001", "Alpha Law")
        r1 = migrate_db(result["db_path"])
        r2 = migrate_db(result["db_path"])
        self.assertEqual(r1["status"], "ok")
        # Second run: no new columns, but should not error
        self.assertNotEqual(r2["status"], "error",
                            "Second migration run must not produce an error")


# ===========================================================================
# TEST 6: Backwards Compatibility
# ===========================================================================

class TestBackwardsCompatibility(Phase4ATestBase):

    def test_database_class_still_works_without_firm_id(self):
        """Database() with no db_path should still initialise correctly."""
        from database import Database
        db_path = os.path.join(self.test_dir, "legacy_test.db")
        db = Database(db_path=db_path)
        self.assertTrue(os.path.exists(db_path))

    def test_existing_admin_staff_roles_work(self):
        """admin and staff users must still be createable and verifiable."""
        from database import Database
        from provisioning import provision_firm
        result = provision_firm("FIRM001", "Alpha Law")
        db = Database(db_path=result["db_path"])
        db.create_user("admin_user", "SecurePass1!", "admin")
        db.create_user("staff_user", "SecurePass2!", "staff")
        admin = db.get_user_by_username("admin_user")
        staff = db.get_user_by_username("staff_user")
        self.assertEqual(admin["role"], "admin")
        self.assertEqual(staff["role"], "staff")

    def test_platform_db_uses_separate_file(self):
        """platform.db must be separate from any solicitor_ledger.db."""
        from platform_db import PlatformDB
        from provisioning import provision_firm
        pdb = PlatformDB()
        result = provision_firm("FIRM001", "Alpha Law")
        platform_path = os.path.realpath(pdb.db_path)
        firm_path = os.path.realpath(result["db_path"])
        self.assertNotEqual(platform_path, firm_path,
                            "platform.db must be a different file from any firm DB")


# ===========================================================================
# Test runner
# ===========================================================================

def run_all_tests():
    """Run all Phase 4A tests and print a summary."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestPlatformDB,
        TestProvisioning,
        TestDBRouter,
        TestTenantIsolation,
        TestRoleFoundation,
        TestBackwardsCompatibility,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
