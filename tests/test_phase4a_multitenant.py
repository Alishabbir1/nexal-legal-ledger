"""Phase 4A multi-tenant foundation tests for Nexal Legal."""
import os
import tempfile
import uuid

import pytest

from nexal_platform.isolation import verify_tenant_isolation
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.provision import provision_firm
from nexal_platform.router import TenantRouter
from nexal_platform.template import ensure_template_database
from phase4a_migrate import migrate_legacy_database


@pytest.fixture()
def isolated_data_root(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-data")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        yield root


def test_platform_schema_creates_firms_workspaces_users(isolated_data_root):
    platform = PlatformDatabase()
    conn = platform.get_connection()
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "firms" in tables
    assert "workspaces" in tables
    assert "users" in tables


def test_template_database_is_solicitor_ledger(isolated_data_root):
    template_path = ensure_template_database()
    assert os.path.isfile(template_path)
    assert template_path.endswith("solicitor_ledger.db")


def test_provision_firm_creates_isolated_workspace(isolated_data_root):
    slug = f"test-firm-{uuid.uuid4().hex[:8]}"
    result = provision_firm(
        name="Test Firm LLP",
        slug=slug,
        firm_code="FIRM999",
        owner_email="owner@testfirm.example",
        portal_user_id="portal-user-123",
    )

    assert result["firm"]["slug"] == slug
    assert result["firm"]["firm_code"] == "FIRM999"
    assert os.path.isfile(result["database_path"])
    assert result["database_path"].endswith("solicitor_ledger.db")
    assert result["workspace"]["database_path"] == result["database_path"]
    assert result["platform_user"]["portal_user_id"] == "portal-user-123"


def test_router_resolves_firm_by_code(isolated_data_root):
    slug = f"router-firm-{uuid.uuid4().hex[:8]}"
    result = provision_firm(name="Router Firm", slug=slug, firm_code="FIRM888")
    router = TenantRouter()

    firm, db = router.get_database_for_code("FIRM888")
    assert firm["id"] == result["firm"]["id"]
    assert db.db_path == result["database_path"]


def test_tenant_isolation_verification(isolated_data_root):
    result = verify_tenant_isolation(paths_root=isolated_data_root)
    assert result["passed"] is True


def test_backwards_compatibility_legacy_database(isolated_data_root):
    from database import Database

    db = Database()
    conn = db.get_connection()
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()


def test_phase4a_validation_runner(isolated_data_root, monkeypatch):
    from phase4a_test import run_phase4a_tests

    summary = run_phase4a_tests(data_root=isolated_data_root)
    assert summary["passed"] is True
