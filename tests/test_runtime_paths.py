"""Runtime path resolution — forbidden deploy paths and workspace remapping."""
import os
import tempfile

import pytest

from nexal_platform.config import (
    PRODUCTION_DATA_ROOT,
    get_platform_paths,
    get_runtime_data_root,
    is_forbidden_runtime_path,
    resolve_workspace_database_path,
    safe_makedirs,
)
from nexal_platform.platform_db import PlatformDatabase


def test_is_forbidden_runtime_path_detects_root_repo():
    assert is_forbidden_runtime_path("/root/nexal-legal-ledger")
    assert is_forbidden_runtime_path("/root/nexal-legal-ledger/data/tenants/x/solicitor_ledger.db")
    assert is_forbidden_runtime_path("/var/lib/nexal-legal/tenants/x/solicitor_ledger.db") is False


def test_default_runtime_root_without_env_is_production_path(monkeypatch):
    monkeypatch.delenv("NEXAL_DATA_DIR", raising=False)
    monkeypatch.delenv("NEXAL_DEV", raising=False)
    root = get_runtime_data_root()
    assert root.replace("\\", "/") == "/var/lib/nexal-legal"


def test_tenant_router_remaps_forbidden_workspace_path(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_root = os.path.join(tmp, "runtime-data")
        monkeypatch.setenv("NEXAL_DATA_DIR", data_root)

        from db_router import reset_router

        reset_router()

        platform = PlatformDatabase()
        firm = platform.create_firm(name="Router Remap Firm", slug="router-remap-firm")
        forbidden = "/root/nexal-legal-ledger/data/tenants/{}/solicitor_ledger.db".format(firm["id"])
        platform.create_workspace(firm_id=firm["id"], database_path=forbidden)

        from nexal_platform.router import TenantRouter

        router = TenantRouter()
        resolved = router.resolve_database_path(firm["id"])
        assert not is_forbidden_runtime_path(resolved)
        assert resolved.startswith(data_root)

        workspace = platform.get_workspace_for_firm(firm["id"])
        assert workspace["database_path"] == resolved


def test_tenant_database_is_valid_never_stats_forbidden_path():
    """Must not raise PermissionError when checking a /root deploy path."""
    from nexal_platform.portal_link import _tenant_database_is_valid

    assert _tenant_database_is_valid("/root/nexal-legal-ledger/data/tenants/x/solicitor_ledger.db") is False


def test_sso_repairs_workspace_path_under_root(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_root = os.path.join(tmp, "runtime-data")
        monkeypatch.setenv("NEXAL_DATA_DIR", data_root)
        monkeypatch.setenv("SSO_SECRET_KEY", "path-test-secret")

        from db_router import reset_router

        reset_router()

        from nexal_platform.platform_db import PlatformDatabase
        from sso_auth import generate_sso_token

        platform = PlatformDatabase()
        firm = platform.create_firm(
            name="Path Repair Firm",
            slug="path-repair-firm",
            portal_firm_id="498205b5-0d17-453c-a0de-e507955e94fb",
        )
        forbidden = "/root/nexal-legal-ledger/data/tenants/{}/solicitor_ledger.db".format(firm["id"])
        platform.create_workspace(firm_id=firm["id"], database_path=forbidden)

        from app import app

        token = generate_sso_token(
            user_id="2cbf9a7d-2f8f-4c4a-9d64-fd7a24d363cc",
            email="sunthessmunir@gmail.com",
            firm_id="498205b5-0d17-453c-a0de-e507955e94fb",
            role="firm_admin",
            username="sunthessmunir",
            extra={
                "firm_name": "new",
                "subscription_tier": "essential",
                "portal_customer_id": "7a0a8a6e-dfc2-444e-9bd0-10e13af27035",
            },
        )

        client = app.test_client()
        response = client.get("/auth/sso?token=" + token)
        assert response.status_code == 302

        workspace = platform.get_workspace_for_firm(firm["id"])
        assert not is_forbidden_runtime_path(workspace["database_path"])
        assert workspace["database_path"].startswith(data_root)
        assert os.path.isfile(workspace["database_path"])


def test_resolve_workspace_database_path_updates_platform_db():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["NEXAL_DATA_DIR"] = os.path.join(tmp, "data")
        platform = PlatformDatabase()
        paths = get_platform_paths()
        firm = platform.create_firm(name="Migrate Firm", slug="migrate-firm")
        forbidden = "/root/nexal-legal-ledger/data/tenants/x/solicitor_ledger.db"
        platform.create_workspace(firm_id=firm["id"], database_path=forbidden)

        resolved = resolve_workspace_database_path(
            platform,
            firm["id"],
            forbidden,
            paths,
        )
        assert resolved == paths.tenant_db_path(firm["id"])
        workspace = platform.get_workspace_for_firm(firm["id"])
        assert workspace["database_path"] == resolved


def test_forbidden_nexal_data_dir_env_falls_back_to_production_root(monkeypatch):
    """Production misconfig: NEXAL_DATA_DIR=/root/nexal-legal-ledger must never be used."""
    monkeypatch.setenv("NEXAL_DATA_DIR", "/root/nexal-legal-ledger")
    root = get_runtime_data_root()
    assert root.replace("\\", "/") == PRODUCTION_DATA_ROOT


def test_get_platform_paths_never_uses_forbidden_root(monkeypatch, tmp_path):
    """get_platform_paths must not call os.makedirs under /root."""
    monkeypatch.setenv("NEXAL_DATA_DIR", str(tmp_path / "allowed-data"))
    paths = get_platform_paths()
    assert not is_forbidden_runtime_path(paths.root)
    assert paths.root.startswith(str(tmp_path))


def test_safe_makedirs_refuses_forbidden_path():
    with pytest.raises(PermissionError, match="forbidden"):
        safe_makedirs("/root/nexal-legal-ledger/data/tenants/x", context="test")


def test_resolve_workspace_repairs_bare_root_directory_path():
    """Legacy rows may store the deploy root itself as database_path."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["NEXAL_DATA_DIR"] = os.path.join(tmp, "data")
        platform = PlatformDatabase()
        paths = get_platform_paths()
        firm = platform.create_firm(name="Bare Root Firm", slug="bare-root-firm")
        platform.create_workspace(firm_id=firm["id"], database_path="/root/nexal-legal-ledger")

        resolved = resolve_workspace_database_path(
            platform,
            firm["id"],
            "/root/nexal-legal-ledger",
            paths,
        )
        assert resolved == paths.tenant_db_path(firm["id"])
        assert not is_forbidden_runtime_path(resolved)
        workspace = platform.get_workspace_for_firm(firm["id"])
        assert workspace["database_path"] == resolved
