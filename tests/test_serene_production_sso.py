"""Serene Solicitors production SSO after legacy migration."""
import os
import shutil
import tempfile
import uuid

import pytest

from nexal_platform.config import get_platform_paths
from nexal_platform.migration.legacy_tenant_import import (
    EXPECTED_APRIL_CASHBOOK,
    migrate_legacy_into_existing_tenant,
)
from nexal_platform.migration.tenant_db_relocate import (
    repair_firm_tenant_database_path,
    tenant_client_count,
)
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.provision import provision_firm

DESKTOP_LEGACY = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "SolicitorLedger",
    "solicitor_ledger.db",
)

SERENE_PORTAL_FIRM_ID = "0343a4a2-5c8e-45ac-a506-61d2dde6fdb3"
SERENE_EMAIL = "Smalik34@hotmail.co.uk"
SERENE_PORTAL_USER_ID = "df47eeee-32fc-4d63-b01e-71b784878465"
SERENE_PORTAL_CUSTOMER_ID = "0ef7eaf6-8825-49c9-901f-e727ea85c1a5"


@pytest.fixture()
def serene_data_root(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "serene-sso")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "serene-prod-sso-test")
        monkeypatch.delenv("NEXAL_DEV", raising=False)
        yield root
        from db_router import reset_router

        reset_router()


@pytest.mark.skipif(not os.path.isfile(DESKTOP_LEGACY), reason="Desktop legacy DB not present")
def test_serene_sso_after_legacy_migration(serene_data_root):
    from db_router import reset_router
    from sso_auth import generate_sso_token
    from app import app

    reset_router()
    provisioned = provision_firm(
        name="Serene Solicitors Limited",
        slug=f"serene-{uuid.uuid4().hex[:8]}",
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        owner_email=SERENE_EMAIL,
        portal_user_id=SERENE_PORTAL_USER_ID,
    )
    firm_id = provisioned["firm"]["id"]

    result = migrate_legacy_into_existing_tenant(
        legacy_path=DESKTOP_LEGACY,
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        owner_email=SERENE_EMAIL,
        portal_user_id=SERENE_PORTAL_USER_ID,
        dry_run=False,
    )
    assert result.validation_passed, result.validation_errors
    assert result.after_snapshot.table_counts["clients"] == 42

    token = generate_sso_token(
        user_id=SERENE_PORTAL_USER_ID,
        email=SERENE_EMAIL,
        firm_id=SERENE_PORTAL_FIRM_ID,
        role="firm_admin",
        username="Smalik34",
        extra={
            "firm_name": "Serene Solicitors Limited",
            "subscription_tier": "essential",
            "portal_customer_id": SERENE_PORTAL_CUSTOMER_ID,
        },
    )

    client = app.test_client()
    response = client.get("/auth/sso?token=" + token)
    assert response.status_code == 302, response.get_data(as_text=True)
    assert response.headers.get("Location") == "/client-ledger"

    dashboard = client.get("/client-ledger")
    assert dashboard.status_code == 200


@pytest.mark.skipif(not os.path.isfile(DESKTOP_LEGACY), reason="Desktop legacy DB not present")
def test_serene_sso_repairs_migrated_db_at_stale_workspace_path(serene_data_root):
    """Migrated data at stale stored path must be relocated before SSO."""
    from db_router import get_db_for_firm, reset_router
    from sso_auth import generate_sso_token
    from app import app

    reset_router()
    provisioned = provision_firm(
        name="Serene Solicitors Limited",
        slug=f"serene-stale-{uuid.uuid4().hex[:8]}",
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        owner_email=SERENE_EMAIL,
        portal_user_id=SERENE_PORTAL_USER_ID,
    )
    firm_id = provisioned["firm"]["id"]
    paths = get_platform_paths()
    canonical = paths.tenant_db_path(firm_id)
    stale_dir = os.path.join(paths.tenants_dir, firm_id, "stale")
    stale_path = os.path.join(stale_dir, "solicitor_ledger.db")

    result = migrate_legacy_into_existing_tenant(
        legacy_path=DESKTOP_LEGACY,
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        dry_run=False,
    )
    migrated_path = result.tenant_database_path
    os.makedirs(stale_dir, exist_ok=True)
    shutil.copy2(migrated_path, stale_path)
    if os.path.isfile(canonical):
        os.remove(canonical)

    platform = PlatformDatabase()
    platform.update_workspace_database_path(firm_id, stale_path)
    assert tenant_client_count(canonical) == 0

    repaired = repair_firm_tenant_database_path(
        platform,
        firm_id,
        min_clients=42,
        allow_global_scan=True,
    )
    assert os.path.abspath(repaired) == os.path.abspath(canonical)
    assert tenant_client_count(canonical) == 42

    reset_router()
    db = get_db_for_firm(firm_id)
    db._security_columns_initialized = True
    db._security_columns_mtime = 0
    db.initialize_security_columns()

    token = generate_sso_token(
        user_id=SERENE_PORTAL_USER_ID,
        email=SERENE_EMAIL,
        firm_id=SERENE_PORTAL_FIRM_ID,
        role="firm_admin",
        username="Smalik34",
        extra={
            "firm_name": "Serene Solicitors Limited",
            "subscription_tier": "essential",
            "portal_customer_id": SERENE_PORTAL_CUSTOMER_ID,
        },
    )
    response = app.test_client().get("/auth/sso?token=" + token)
    assert response.status_code == 302, response.get_data(as_text=True)


def test_initialize_security_columns_reruns_after_db_file_replaced(serene_data_root):
    from database import Database

    paths = get_platform_paths()
    db_path = os.path.join(paths.tenants_dir, "mtime-test", "solicitor_ledger.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    db = Database(db_path=db_path, skip_user_seed=True)
    db.initialize_security_columns()
    conn = db.get_connection()
    cols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    conn.close()
    assert "portal_user_id" in cols

    if not os.path.isfile(DESKTOP_LEGACY):
        pytest.skip("Desktop legacy DB not present")

    shutil.copy2(DESKTOP_LEGACY, db_path)
    db._security_columns_initialized = True
    db._security_columns_mtime = 0
    db.initialize_security_columns()
    conn = db.get_connection()
    cols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    conn.close()
    assert "portal_user_id" in cols
    assert "firm_id" in cols
