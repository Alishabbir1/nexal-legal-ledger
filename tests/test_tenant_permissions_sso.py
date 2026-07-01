"""Tenant DB ownership — root migration vs Gunicorn service user writes."""
import os
import stat
import tempfile
import uuid

import bcrypt
import pytest

from nexal_platform.migration.legacy_tenant_import import migrate_legacy_into_existing_tenant
from nexal_platform.provision import provision_firm
from sso_auth import generate_sso_token

DESKTOP_LEGACY = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "SolicitorLedger",
    "solicitor_ledger.db",
)

SERENE_PORTAL_FIRM_ID = "0343a4a2-5c8e-45ac-a506-61d2dde6fdb3"


@pytest.fixture()
def serene_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "ownership")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "ownership-test")
        monkeypatch.delenv("NEXAL_DEV", raising=False)
        yield root
        from db_router import reset_router

        reset_router()


@pytest.mark.skipif(not os.path.isfile(DESKTOP_LEGACY), reason="Desktop legacy DB not present")
@pytest.mark.skipif(os.name != "posix", reason="Unix permissions semantics")
def test_readonly_tenant_db_causes_sso_db_error_on_portal_post(serene_env):
    """Root-owned read-only tenant DB fails Portal-style POST SSO with sqlite error."""
    from app import app

    provision_firm(
        name="Serene Solicitors Limited",
        slug=f"serene-ro-{uuid.uuid4().hex[:8]}",
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        owner_email="Smalik34@hotmail.co.uk",
        portal_user_id="df47eeee-32fc-4d63-b01e-71b784878465",
    )
    result = migrate_legacy_into_existing_tenant(
        legacy_path=DESKTOP_LEGACY,
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        dry_run=False,
    )
    db_path = result.tenant_database_path
    os.chmod(db_path, stat.S_IRUSR | stat.S_IRGRP)

    portal_hash = bcrypt.hashpw(b"PortalPass99!", bcrypt.gensalt(12)).decode()
    token = generate_sso_token(
        user_id="df47eeee-32fc-4d63-b01e-71b784878465",
        email="Smalik34@hotmail.co.uk",
        firm_id=SERENE_PORTAL_FIRM_ID,
        role="firm_admin",
        username="Smalik34",
        extra={
            "password_hash": portal_hash,
            "portal_customer_id": "0ef7eaf6-8825-49c9-901f-e727ea85c1a5",
            "first_name": "Serene",
            "last_name": "Admin",
            "max_users": 2,
            "account_status": "ACTIVE",
        },
    )

    response = app.test_client().post("/auth/sso", data={"token": token})
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["code"] == "SSO_DB_ERROR"


@pytest.mark.skipif(not os.path.isfile(DESKTOP_LEGACY), reason="Desktop legacy DB not present")
def test_portal_post_sso_after_migration(serene_env):
    from app import app

    provision_firm(
        name="Serene Solicitors Limited",
        slug=f"serene-post-{uuid.uuid4().hex[:8]}",
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        owner_email="Smalik34@hotmail.co.uk",
        portal_user_id="df47eeee-32fc-4d63-b01e-71b784878465",
    )
    migrate_legacy_into_existing_tenant(
        legacy_path=DESKTOP_LEGACY,
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        dry_run=False,
    )

    portal_hash = bcrypt.hashpw(b"PortalPass99!", bcrypt.gensalt(12)).decode()
    token = generate_sso_token(
        user_id="df47eeee-32fc-4d63-b01e-71b784878465",
        email="Smalik34@hotmail.co.uk",
        firm_id=SERENE_PORTAL_FIRM_ID,
        role="firm_admin",
        username="Smalik34",
        extra={
            "password_hash": portal_hash,
            "portal_customer_id": "0ef7eaf6-8825-49c9-901f-e727ea85c1a5",
            "first_name": "Serene",
            "last_name": "Admin",
            "max_users": 2,
            "account_status": "ACTIVE",
        },
    )
    response = app.test_client().post("/auth/sso", data={"token": token})
    assert response.status_code == 302, response.get_data(as_text=True)
    assert response.headers.get("Location") == "/client-ledger"
