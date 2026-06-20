"""Tenant-aware SSO routing helpers (Phase 4E — direct login removed)."""
import os
import tempfile
import uuid

import bcrypt
import pytest

from app import app
from db_router import reset_router
from lib.tenant_auth import resolve_user_for_login
from nexal_platform.provision import provision_firm
from sso_auth import generate_sso_token


@pytest.fixture()
def tenant_auth_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-tenant-auth")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "tenant-auth-test-secret")
        monkeypatch.setenv("NEXAL_PORTAL_URL", "http://portal.test")
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


def test_login_get_redirects_to_portal_with_stale_firm_id(tenant_auth_env):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["firm_id"] = "00000000-0000-0000-0000-000000000000"

    response = client.get("/login")
    assert response.status_code == 302
    assert "portal.test/login" in response.location


def test_login_get_redirects_to_portal_with_stale_recovery_firm_id(tenant_auth_env):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["recovery_firm_id"] = "00000000-0000-0000-0000-000000000000"

    response = client.get("/login")
    assert response.status_code == 302
    assert "portal.test/login" in response.location


def test_login_post_redirects_to_portal(tenant_auth_env):
    client = app.test_client()
    response = client.post("/login", data={"username": "nobody", "password": "wrong"})
    assert response.status_code == 302
    assert "portal.test/login" in response.location
    assert "direct_login_disabled" in response.location


def test_admin_recovery_redirects_to_portal_login(tenant_auth_env):
    client = app.test_client()
    response = client.post(
        "/admin/recovery",
        data={"username": "nobody", "recovery_key": "SRN-AAAA-BBBB-CCCC"},
    )
    assert response.status_code == 302
    assert "portal.test/login" in response.location


def test_direct_login_post_no_longer_authenticates(tenant_auth_env):
    ctx = _provision_sso_admin("MyLedgerPass1!")
    ctx["client"].get("/logout")

    response = ctx["client"].post(
        "/login",
        data={"username": ctx["email"], "password": ctx["password"]},
    )
    assert response.status_code == 302
    assert "portal.test/login" in response.location
    with ctx["client"].session_transaction() as sess:
        assert not sess.get("user_id")
