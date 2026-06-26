"""
Regression test: TenantRouter must evict stale cache entries that point at
forbidden runtime paths (e.g. /root/nexal-legal-ledger) and re-resolve the
correct path from platform.db after a workspace repair.

Previously, repair_all_stale_workspace_paths() would update platform.db on
disk but the global TenantRouter._cache still held a Database object created
with the forbidden path, causing [Errno 13] Permission denied on every
subsequent SSO attempt for any affected tenant.

This test must pass on every future code change to ensure this class of
runtime path failure is permanently impossible.
"""
import sys
import os
import types
import unittest

# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without the full application
# ---------------------------------------------------------------------------

def _make_stub_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

# database stub
database_mod = _make_stub_module("database")
class _Database:
    def __init__(self, db_path=""):
        self.db_path = db_path
database_mod.Database = _Database

# nexal_platform.platform_db stub
_make_stub_module("nexal_platform")
platform_db_mod = _make_stub_module("nexal_platform.platform_db")
class _PlatformDatabase:
    def __init__(self, paths=None):
        self._workspaces = {}
    def get_workspace_for_firm(self, firm_id):
        ws = self._workspaces.get(firm_id, {})
        if not ws:
            raise KeyError(firm_id)
        return ws
platform_db_mod.PlatformDatabase = _PlatformDatabase

# nexal_platform.config stub with real logic extracted inline
config_mod = _make_stub_module("nexal_platform.config")

PRODUCTION_DATA_ROOT = "/var/lib/nexal-legal"

def is_forbidden_runtime_path(path):
    if not path or not str(path).strip():
        return True
    import os as _os
    normalized = _os.path.normpath(str(path)).replace("\\", "/")
    lower = normalized.lower()
    if lower.startswith("/root/"):
        return True
    repo_marker = "nexal-legal-ledger"
    if repo_marker in lower and not lower.startswith(PRODUCTION_DATA_ROOT.lower()):
        return True
    return False

class _PlatformPaths:
    def __init__(self):
        self.root = PRODUCTION_DATA_ROOT
    def tenant_db_path(self, firm_id):
        return f"{PRODUCTION_DATA_ROOT}/tenants/{firm_id}/solicitor_ledger.db"

def get_platform_paths(root=None):
    return _PlatformPaths()

def resolve_workspace_database_path(platform, firm_id, stored_path, paths):
    canonical = paths.tenant_db_path(firm_id)
    if is_forbidden_runtime_path(stored_path):
        platform._workspaces[firm_id]["database_path"] = canonical
        return canonical
    return stored_path

config_mod.PlatformPaths = _PlatformPaths
config_mod.get_platform_paths = get_platform_paths
config_mod.is_forbidden_runtime_path = is_forbidden_runtime_path
config_mod.resolve_workspace_database_path = resolve_workspace_database_path

# Now import the real router module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nexal_platform.router import TenantRouter  # noqa: E402


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTenantRouterStaleCacheEviction(unittest.TestCase):
    """Reproduce the exact failure: stale cache entry with forbidden path."""

    def _make_router(self, firm_id, initial_path):
        router = TenantRouter.__new__(TenantRouter)
        router.paths = get_platform_paths()
        router.platform = _PlatformDatabase()
        router.platform._workspaces[firm_id] = {
            "status": "active",
            "database_path": initial_path,
        }
        router._cache = {}
        return router

    def test_forbidden_path_in_cache_is_evicted(self):
        """A cached Database with a forbidden path must be evicted on next get_database()."""
        firm_id = "firm-abc"
        forbidden_path = "/root/nexal-legal-ledger"
        correct_path = f"{PRODUCTION_DATA_ROOT}/tenants/{firm_id}/solicitor_ledger.db"

        router = self._make_router(firm_id, correct_path)

        # Manually plant a stale cache entry with the forbidden path
        stale_db = _Database(db_path=forbidden_path)
        router._cache[firm_id] = stale_db

        # Simulate repair: platform.db now has the correct path
        router.platform._workspaces[firm_id]["database_path"] = correct_path

        # get_database() must detect the forbidden cached path, evict it,
        # and return a fresh Database with the correct path.
        db = router.get_database(firm_id)

        self.assertNotEqual(db.db_path, forbidden_path,
                            "Forbidden path must not survive in cache after repair")
        self.assertEqual(db.db_path, correct_path,
                         "Resolved path must be the canonical production path")
        self.assertIsNot(db, stale_db,
                         "A new Database object must have been created")

    def test_valid_cached_path_is_reused(self):
        """A cached Database with a valid path must be returned without re-resolution."""
        firm_id = "firm-xyz"
        correct_path = f"{PRODUCTION_DATA_ROOT}/tenants/{firm_id}/solicitor_ledger.db"

        router = self._make_router(firm_id, correct_path)

        # Prime the cache with a valid Database
        good_db = _Database(db_path=correct_path)
        router._cache[firm_id] = good_db

        db = router.get_database(firm_id)

        self.assertIs(db, good_db, "Valid cached Database must be returned as-is")

    def test_repo_path_in_cache_is_evicted(self):
        """A cached Database path containing the repo marker must also be evicted."""
        firm_id = "firm-repo"
        repo_path = "/home/deploy/nexal-legal-ledger/data/tenants/firm-repo/ledger.db"
        correct_path = f"{PRODUCTION_DATA_ROOT}/tenants/{firm_id}/solicitor_ledger.db"

        router = self._make_router(firm_id, correct_path)
        router._cache[firm_id] = _Database(db_path=repo_path)
        router.platform._workspaces[firm_id]["database_path"] = correct_path

        db = router.get_database(firm_id)

        self.assertNotEqual(db.db_path, repo_path)
        self.assertEqual(db.db_path, correct_path)

    def test_none_db_path_in_cache_is_evicted(self):
        """A cached Database with db_path=None must be evicted."""
        firm_id = "firm-none"
        correct_path = f"{PRODUCTION_DATA_ROOT}/tenants/{firm_id}/solicitor_ledger.db"

        router = self._make_router(firm_id, correct_path)
        router._cache[firm_id] = _Database(db_path=None)
        router.platform._workspaces[firm_id]["database_path"] = correct_path

        db = router.get_database(firm_id)

        self.assertEqual(db.db_path, correct_path)


if __name__ == "__main__":
    unittest.main()
