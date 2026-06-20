"""Recovery key generation — no password re-entry required."""
import os
import tempfile
import uuid

import pytest
from werkzeug.security import generate_password_hash

from app import app, verify_recovery_key
from db_router import get_db_for_firm, reset_router
from nexal_platform.provision import provision_firm
from sso_auth import generate_sso_token


@pytest.fixture()
def recovery_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-recovery-key")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "recovery-key-test-secret")
        reset_router()
        yield root


def _sso_admin_client(portal_firm_id=None, username="admin"):
    portal_firm_id = portal_firm_id or str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    portal_hash = generate_password_hash("PortalPass1!", method="scrypt")

    provision_firm(
        name="Recovery Test Firm",
        slug=f"rk-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )

    token = generate_sso_token(
        user_id=portal_user_id,
        email=f"{username}@recovery.example",
        firm_id=portal_firm_id,
        role="firm_admin",
        username=username,
        extra={"password_hash": portal_hash},
    )

    client = app.test_client()
    assert client.get("/auth/sso?token=" + token).status_code == 302
    return client, portal_firm_id, username


def test_admin_generates_recovery_key_without_password(recovery_env):
    client, _, _ = _sso_admin_client()

    response = client.post("/admin/security/generate-recovery-key")
    assert response.status_code == 302

    with client.session_transaction() as sess:
        pending = sess.get("pending_recovery_key")
        assert pending
        assert pending.startswith("SRN-")


def test_regenerating_invalidates_old_recovery_key(recovery_env):
    client, _, _ = _sso_admin_client()

    client.post("/admin/security/generate-recovery-key")
    with client.session_transaction() as sess:
        first_key = sess["pending_recovery_key"]
        firm_id = sess["firm_id"]
        user_id = sess["user_id"]

    db = get_db_for_firm(firm_id)
    first_hash = db.get_user_by_id(user_id)["admin_recovery_key_hash"]
    assert verify_recovery_key(first_hash, first_key)

    client.post("/admin/security/recovery-key-ack")
    client.post("/admin/security/generate-recovery-key")
    with client.session_transaction() as sess:
        second_key = sess["pending_recovery_key"]

    second_hash = db.get_user_by_id(user_id)["admin_recovery_key_hash"]
    assert verify_recovery_key(second_hash, second_key)
    assert not verify_recovery_key(second_hash, first_key)


def test_new_recovery_key_works_for_admin_recovery(recovery_env):
    client, _, username = _sso_admin_client()

    client.post("/admin/security/generate-recovery-key")
    with client.session_transaction() as sess:
        recovery_key = sess["pending_recovery_key"]

    client.post("/admin/security/recovery-key-ack")

    response = client.post(
        "/admin/recovery",
        data={"username": username, "recovery_key": recovery_key},
    )
    assert response.status_code == 302
    assert "/admin/recovery/reset" in response.location

    with client.session_transaction() as sess:
        assert sess.get("recovery_username") == username


def test_audit_log_records_recovery_key_generation(recovery_env):
    client, _, username = _sso_admin_client()

    with client.session_transaction() as sess:
        firm_id = sess["firm_id"]

    client.post("/admin/security/generate-recovery-key")

    db = get_db_for_firm(firm_id)
    entries = db.get_audit_log_entries(module="Security", limit=10)
    assert any(e["action"] == "Recovery key generated" for e in entries)
    match = next(e for e in entries if e["action"] == "Recovery key generated")
    assert username in (match.get("details") or "")
    assert match.get("username") == username


def test_non_admin_cannot_generate_recovery_key(recovery_env):
    portal_firm_id = str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    portal_hash = generate_password_hash("StaffPass1!", method="scrypt")

    provision_firm(
        name="Staff Firm",
        slug=f"staff-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )

    token = generate_sso_token(
        user_id=portal_user_id,
        email="staff@recovery.example",
        firm_id=portal_firm_id,
        role="staff",
        username="staffuser",
        extra={"password_hash": portal_hash},
    )

    client = app.test_client()
    assert client.get("/auth/sso?token=" + token).status_code == 302

    response = client.post("/admin/security/generate-recovery-key")
    assert response.status_code == 302
    assert "client-ledger" in response.location or "client_ledger" in response.location

    with client.session_transaction() as sess:
        assert not sess.get("pending_recovery_key")


def test_unauthenticated_user_cannot_generate_recovery_key(recovery_env):
    client = app.test_client()
    response = client.post("/admin/security/generate-recovery-key")
    assert response.status_code in (302, 401, 403)
