"""Regression tests for account lifecycle resume → SSO launch."""
import os
import tempfile
import uuid

import pytest

from db_router import reset_router
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.portal_link import resolve_active_portal_firm
from nexal_platform.provision import provision_firm


@pytest.fixture()
def phase4c_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-lifecycle-sso")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "lifecycle-sso-test-secret")
        reset_router()
        yield root
        reset_router()


def test_resolve_active_portal_firm_reactivates_suspended_firm_on_active_jwt(phase4c_env):
    portal_firm_id = str(uuid.uuid4())
    platform = PlatformDatabase()
    result = provision_firm(
        name="Resume Test Firm",
        slug="resume-test-" + portal_firm_id[:8],
        portal_firm_id=portal_firm_id,
        owner_email="resume@example.com",
    )
    firm_id = result["firm"]["id"]

    platform.update_firm_status_by_portal_firm_id(portal_firm_id, "suspended")
    suspended = platform.get_firm(firm_id)
    assert suspended["status"] == "suspended"
    workspace = platform.get_workspace_for_firm(firm_id)
    assert workspace["status"] == "suspended"

    jwt_payload = {
        "sub": str(uuid.uuid4()),
        "email": "resume@example.com",
        "firm_id": portal_firm_id,
        "account_status": "ACTIVE",
    }

    resolved = resolve_active_portal_firm(portal_firm_id, jwt_payload)
    assert resolved["id"] == firm_id
    assert resolved["status"] == "active"
    reactivated_workspace = platform.get_workspace_for_firm(firm_id)
    assert reactivated_workspace["status"] == "active"


def test_resolve_active_portal_firm_keeps_suspended_without_active_jwt(phase4c_env):
    portal_firm_id = str(uuid.uuid4())
    platform = PlatformDatabase()
    provision_firm(
        name="Paused Firm",
        slug="paused-" + portal_firm_id[:8],
        portal_firm_id=portal_firm_id,
        owner_email="paused@example.com",
    )
    platform.update_firm_status_by_portal_firm_id(portal_firm_id, "suspended")

    with pytest.raises(ValueError, match="not active"):
        resolve_active_portal_firm(portal_firm_id, None)
