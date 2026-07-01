"""Tests for one-time legacy tenant import."""
import os
import shutil
import tempfile
import uuid

import pytest

from nexal_platform.config import get_platform_paths
from nexal_platform.migration.legacy_tenant_import import (
    EXPECTED_APRIL_CASHBOOK,
    migrate_legacy_into_existing_tenant,
    snapshot_tenant,
)
from nexal_platform.provision import provision_firm

DESKTOP_LEGACY = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "SolicitorLedger",
    "solicitor_ledger.db",
)

SERENE_PORTAL_FIRM_ID = "0343a4a2-5c8e-45ac-a506-61d2dde6fdb3"


@pytest.fixture()
def isolated_data_root(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-migration")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.delenv("NEXAL_DEV", raising=False)
        yield root
        from db_router import reset_router

        reset_router()


@pytest.mark.skipif(not os.path.isfile(DESKTOP_LEGACY), reason="Desktop legacy DB not present")
def test_serene_legacy_snapshot_april_balance(isolated_data_root):
    snap = snapshot_tenant(DESKTOP_LEGACY)
    assert snap.table_counts.get("clients", 0) > 0
    assert snap.cashbook_balance == EXPECTED_APRIL_CASHBOOK
    assert snap.april_reconciliation is not None
    assert snap.april_reconciliation["cashbook_total"] == float(EXPECTED_APRIL_CASHBOOK)


@pytest.mark.skipif(not os.path.isfile(DESKTOP_LEGACY), reason="Desktop legacy DB not present")
def test_migrate_serene_legacy_into_existing_tenant(isolated_data_root):
    slug = f"serene-{uuid.uuid4().hex[:8]}"
    provisioned = provision_firm(
        name="Serene Solicitors Limited",
        slug=slug,
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        owner_email="Smalik34@hotmail.co.uk",
        portal_user_id="df47eeee-32fc-4d63-b01e-71b784878465",
    )

    dry = migrate_legacy_into_existing_tenant(
        legacy_path=DESKTOP_LEGACY,
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        owner_email="Smalik34@hotmail.co.uk",
        portal_user_id="df47eeee-32fc-4d63-b01e-71b784878465",
        dry_run=True,
    )
    assert dry.validation_passed, dry.validation_errors

    result = migrate_legacy_into_existing_tenant(
        legacy_path=DESKTOP_LEGACY,
        portal_firm_id=SERENE_PORTAL_FIRM_ID,
        owner_email="Smalik34@hotmail.co.uk",
        portal_user_id="df47eeee-32fc-4d63-b01e-71b784878465",
        dry_run=False,
    )
    assert result.validation_passed, result.validation_errors
    assert result.after_snapshot.cashbook_balance == EXPECTED_APRIL_CASHBOOK
    assert result.after_snapshot.table_counts["clients"] == 42
    assert result.after_snapshot.table_counts["ledger_transactions"] == 178
    assert os.path.isfile(result.tenant_database_path)

    paths = get_platform_paths()
    tenant_path = paths.tenant_db_path(provisioned["firm"]["id"])
    assert os.path.abspath(result.tenant_database_path) == os.path.abspath(tenant_path)
