"""Tenant-aware direct login and admin recovery authentication."""
import os
import tempfile
import uuid

import bcrypt
import pytest
from werkzeug.security import generate_password_hash

from app import app, verify_recovery_key
from db_router import get_db_for_firm, reset_router
from lib.tenant_auth import resolve_admin_for_recovery, resolve_user_for_login
from nexal_platform.provision import provision_firm
from sso_auth import generate_sso_token


@pytest.fixture()
def tenant_auth_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-tenant-auth")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "tenant-auth-test-secret")
        reset_router()
        yield root


def _provision_sso_admin(password: str = "DirectLogin99!"):
    portal_firm_id = str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    portal_hash = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")
    email = "admin@tenant-auth.example"

    result = provision_firm(
        name="Tenant Auth Firm",
        slug=f"ta-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )
    firm_id = result["firm"]["id"]

    token = generate_sso_token(
        user_id=portal_user_id,
        email=email,
        firm_id=portal_firm_id,
        role="firm_admin",
        username="tenantadmin",
        extra={"password_hash": portal_hash},
    )
    client = app.test_client()
    assert client.get("/auth/sso?token=" + token).status_code == 302
    with client.session_transaction() as sess:
        username = sess["username"]
        user_id = sess["user_id"]
    return {
        "client": client,
        "firm_id": firm_id,
        "username": username,
        "email": email,
        "password": password,
        "user_id": user_id,
        "portal_hash": portal_hash,
    }


def test_resolve_user_for_login_finds_tenant_user_by_username(tenant_auth_env):
    ctx = _provision_sso_admin()
    client = ctx["client"]
    client.get("/logout")

    resolved = resolve_user_for_login(ctx["username"])
    assert resolved is not None
    user, firm_id, auth_db = resolved
    assert user["user_id"] == ctx["user_id"]
    assert firm_id == ctx["firm_id"]
    assert auth_db.get_user_by_id(user["user_id"]) is not None


def test_resolve_user_for_login_finds_tenant_user_by_email(tenant_auth_env):
    ctx = _provision_sso_admin()
    ctx["client"].get("/logout")

    resolved = resolve_user_for_login(ctx["email"])
    assert resolved is not None
    user, firm_id, _ = resolved
    assert user["user_id"] == ctx["user_id"]
    assert firm_id == ctx["firm_id"]


def test_login_get_returns_200_with_stale_firm_id_session(tenant_auth_env):
    """Stale firm_id in session must not crash the public login page."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["firm_id"] = "00000000-0000-0000-0000-000000000000"

    response = client.get("/login")
    assert response.status_code == 200
    assert b"Sign In" in response.data


def test_login_get_returns_200_with_stale_recovery_firm_id(tenant_auth_env):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["recovery_firm_id"] = "00000000-0000-0000-0000-000000000000"

    response = client.get("/login")
    assert response.status_code == 200
    assert b"Sign In" in response.data


def test_login_get_returns_200_without_session(tenant_auth_env):
    client = app.test_client()
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Sign In" in response.data


def test_direct_login_with_tenant_credentials(tenant_auth_env):
    ctx = _provision_sso_admin("MyLedgerPass1!")
    ctx["client"].get("/logout")

    response = ctx["client"].post(
        "/login",
        data={"username": ctx["email"], "password": ctx["password"]},
    )
    assert response.status_code == 302
    with ctx["client"].session_transaction() as sess:
        assert sess.get("user_id") == ctx["user_id"]
        assert sess.get("firm_id") == ctx["firm_id"]
        assert not sess.get("sso_login")


def test_admin_recovery_works_after_logout_with_fresh_key(tenant_auth_env):
    ctx = _provision_sso_admin("RecoveryFlow88!")
    client = ctx["client"]

    gen = client.post("/admin/security/generate-recovery-key")
    assert gen.status_code == 302
    with client.session_transaction() as sess:
        recovery_key = sess["pending_recovery_key"]

    client.post("/admin/security/recovery-key-ack")
    client.get("/logout")

    response = client.post(
        "/admin/recovery",
        data={"username": ctx["username"], "recovery_key": recovery_key},
    )
    assert response.status_code == 302
    assert "/admin/recovery/reset" in response.location

    with client.session_transaction() as sess:
        assert sess.get("recovery_username") == ctx["username"]
        assert sess.get("recovery_firm_id") == ctx["firm_id"]


def test_admin_recovery_accepts_email_identifier(tenant_auth_env):
    ctx = _provision_sso_admin("EmailRecovery77!")
    client = ctx["client"]

    client.post("/admin/security/generate-recovery-key")
    with client.session_transaction() as sess:
        recovery_key = sess["pending_recovery_key"]
    client.post("/admin/security/recovery-key-ack")
    client.get("/logout")

    response = client.post(
        "/admin/recovery",
        data={"username": ctx["email"], "recovery_key": recovery_key},
    )
    assert response.status_code == 302
    assert "/admin/recovery/reset" in response.location


def test_admin_recovery_reset_updates_tenant_password(tenant_auth_env):
    ctx = _provision_sso_admin("BeforeReset99!")
    client = ctx["client"]

    client.post("/admin/security/generate-recovery-key")
    with client.session_transaction() as sess:
        recovery_key = sess["pending_recovery_key"]
    client.post("/admin/security/recovery-key-ack")
    client.get("/logout")

    client.post(
        "/admin/recovery",
        data={"username": ctx["username"], "recovery_key": recovery_key},
    )
    response = client.post(
        "/admin/recovery/reset",
        data={"new_password": "AfterReset99!", "confirm_password": "AfterReset99!"},
    )
    assert response.status_code == 302

    tenant_db = get_db_for_firm(ctx["firm_id"])
    user = tenant_db.get_user_by_id(ctx["user_id"])
    from lib.password_verification import verify_password

    assert verify_password(user["password_hash"], "AfterReset99!")


def test_stale_recovery_key_rejected_after_regeneration(tenant_auth_env):
    ctx = _provision_sso_admin("StaleKey55!")
    client = ctx["client"]

    client.post("/admin/security/generate-recovery-key")
    with client.session_transaction() as sess:
        old_key = sess["pending_recovery_key"]
    client.post("/admin/security/recovery-key-ack")

    client.post("/admin/security/generate-recovery-key")
    with client.session_transaction() as sess:
        new_key = sess["pending_recovery_key"]
    client.post("/admin/security/recovery-key-ack")
    client.get("/logout")

    tenant_db = get_db_for_firm(ctx["firm_id"])
    user = tenant_db.get_user_by_id(ctx["user_id"])
    key_hash = user["admin_recovery_key_hash"]
    assert verify_recovery_key(key_hash, new_key)
    assert not verify_recovery_key(key_hash, old_key)

    response = client.post(
        "/admin/recovery",
        data={"username": ctx["username"], "recovery_key": old_key},
    )
    assert response.status_code == 200
    assert b"Invalid username or recovery key" in response.data
