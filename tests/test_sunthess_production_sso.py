"""Sunthessmunir production SSO — exact account IDs and forbidden-path repair."""
import os
import sys
import tempfile

import pytest

from nexal_platform.config import get_platform_paths, is_forbidden_runtime_path
from nexal_platform.platform_db import PlatformDatabase

PORTAL_FIRM_ID = "498205b5-0d17-453c-a0de-e507955e94fb"
FIRM_USER_ID = "2cbf9a7d-2f8f-4c4a-9d64-fd7a24d363cc"
CUSTOMER_ID = "7a0a8a6e-dfc2-444e-9bd0-10e13af27035"
EMAIL = "sunthessmunir@gmail.com"
FORBIDDEN_PREFIX = "/root/nexal-legal-ledger/data/tenants/"


def test_get_workspace_for_firm_repairs_forbidden_path_on_read(monkeypatch):
    """Every workspace read must remap stale /root paths before any filesystem access."""
    with tempfile.TemporaryDirectory() as tmp:
        data_root = os.path.join(tmp, "runtime-data")
        monkeypatch.setenv("NEXAL_DATA_DIR", data_root)

        platform = PlatformDatabase()
        firm = platform.create_firm(
            name="new",
            slug="sunthess-path-read",
            portal_firm_id=PORTAL_FIRM_ID,
        )
        forbidden = FORBIDDEN_PREFIX + firm["id"] + "/solicitor_ledger.db"
        platform.create_workspace(firm_id=firm["id"], database_path=forbidden)

        workspace = platform.get_workspace_for_firm(firm["id"])
        assert not is_forbidden_runtime_path(workspace["database_path"])
        assert workspace["database_path"].startswith(data_root)

        reread = platform.get_workspace_for_firm(firm["id"])
        assert reread["database_path"] == workspace["database_path"]


@pytest.mark.skipif(sys.platform == "win32", reason="Linux production PermissionError semantics")
def test_pre_fix_failure_line_is_os_path_isfile_on_root_path():
    """
    Documents production failure before path remapping:
    portal_link._tenant_database_is_valid() line os.path.isfile(db_path)
    when db_path starts with /root/nexal-legal-ledger/.
    """
    db_path = FORBIDDEN_PREFIX + "example-firm/solicitor_ledger.db"
    with pytest.raises(PermissionError):
        os.path.isfile(db_path)


def test_startup_repair_removes_forbidden_paths_before_sso(monkeypatch):
    """PlatformDatabase init must remap stale paths before any SSO request."""
    with tempfile.TemporaryDirectory() as tmp:
        data_root = os.path.join(tmp, "runtime-data")
        monkeypatch.setenv("NEXAL_DATA_DIR", data_root)

        platform = PlatformDatabase()
        firm = platform.create_firm(
            name="new",
            slug="sunthess-startup-repair",
            portal_firm_id=PORTAL_FIRM_ID,
        )
        forbidden = FORBIDDEN_PREFIX + firm["id"] + "/solicitor_ledger.db"
        platform.create_workspace(firm_id=firm["id"], database_path=forbidden)

        PlatformDatabase()

        workspace = PlatformDatabase().get_workspace_for_firm(firm["id"])
        assert not is_forbidden_runtime_path(workspace["database_path"])
        assert workspace["database_path"].startswith(data_root)


def test_sunthess_production_sso_end_to_end(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_root = os.path.join(tmp, "runtime-data")
        monkeypatch.setenv("NEXAL_DATA_DIR", data_root)
        monkeypatch.setenv("SSO_SECRET_KEY", "sunthess-prod-test")

        from db_router import reset_router

        reset_router()

        from nexal_platform.provision import provision_firm
        from sso_auth import generate_sso_token
        from app import app

        result = provision_firm(
            name="new",
            slug="new-sunthess-prod",
            portal_firm_id=PORTAL_FIRM_ID,
            owner_email=EMAIL,
            portal_user_id=CUSTOMER_ID,
            subscription_tier="essential",
        )
        firm_id = result["firm"]["id"]
        forbidden = FORBIDDEN_PREFIX + firm_id + "/solicitor_ledger.db"
        PlatformDatabase().update_workspace_database_path(firm_id, forbidden)

        token = generate_sso_token(
            user_id=FIRM_USER_ID,
            email=EMAIL,
            firm_id=PORTAL_FIRM_ID,
            role="firm_admin",
            username="sunthessmunir",
            extra={
                "firm_name": "new",
                "subscription_tier": "essential",
                "portal_customer_id": CUSTOMER_ID,
            },
        )

        client = app.test_client()
        response = client.get("/auth/sso?token=" + token)
        assert response.status_code == 302
        assert response.headers.get("Location") == "/client-ledger"

        workspace = PlatformDatabase().get_workspace_for_firm(firm_id)
        assert not is_forbidden_runtime_path(workspace["database_path"])
        assert os.path.isfile(workspace["database_path"])

        dashboard = client.get("/client-ledger")
        assert dashboard.status_code == 200


def test_sunthess_sso_repairs_bare_root_database_path(monkeypatch):
    """Exact production error path: database_path = /root/nexal-legal-ledger."""
    with tempfile.TemporaryDirectory() as tmp:
        data_root = os.path.join(tmp, "runtime-data")
        monkeypatch.setenv("NEXAL_DATA_DIR", data_root)
        monkeypatch.setenv("SSO_SECRET_KEY", "sunthess-bare-root")

        from db_router import reset_router

        reset_router()

        from nexal_platform.provision import provision_firm
        from sso_auth import generate_sso_token
        from app import app

        result = provision_firm(
            name="new",
            slug="sunthess-bare-root",
            portal_firm_id=PORTAL_FIRM_ID,
            owner_email=EMAIL,
            portal_user_id=CUSTOMER_ID,
            subscription_tier="essential",
        )
        firm_id = result["firm"]["id"]
        PlatformDatabase().update_workspace_database_path(firm_id, "/root/nexal-legal-ledger")

        token = generate_sso_token(
            user_id=FIRM_USER_ID,
            email=EMAIL,
            firm_id=PORTAL_FIRM_ID,
            role="firm_admin",
            username="sunthessmunir",
            extra={
                "firm_name": "new",
                "subscription_tier": "essential",
                "portal_customer_id": CUSTOMER_ID,
            },
        )

        client = app.test_client()
        response = client.get("/auth/sso?token=" + token)
        assert response.status_code == 302, response.get_data(as_text=True)

        workspace = PlatformDatabase().get_workspace_for_firm(firm_id)
        assert not is_forbidden_runtime_path(workspace["database_path"])
        assert workspace["database_path"].startswith(data_root)
        assert os.path.isfile(workspace["database_path"])
