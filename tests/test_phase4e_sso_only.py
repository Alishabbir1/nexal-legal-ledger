"""Phase 4E — SSO-only authentication (Portal as identity provider)."""
import os
import tempfile
import uuid

import bcrypt
import pytest

from app import app
from db_router import get_db_for_firm, reset_router
from lib.portal_auth import (
    get_portal_dashboard_url,
    get_portal_login_url,
)
from nexal_platform.provision import provision_firm
from sso_auth import generate_sso_token

PORTAL_TEST_URL = "http://portal.test"


@pytest.fixture()
def phase4e_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-phase4e")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "phase4e-test-secret")
        monkeypatch.setenv("NEXAL_PORTAL_URL", PORTAL_TEST_URL)
        reset_router()
        yield root
        reset_router()


def _provision_and_sso(portal_firm_id=None, username="owner", email=None):
    portal_firm_id = portal_firm_id or str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    email = email or f"{username}@phase4e.example"
    portal_hash = bcrypt.hashpw(b"PortalPass99!", bcrypt.gensalt(12)).decode()

    result = provision_firm(
        name="Phase 4E Firm",
        slug=f"p4e-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )
    firm_id = result["firm"]["id"]

    token = generate_sso_token(
        user_id=portal_user_id,
        email=email,
        firm_id=portal_firm_id,
        role="firm_admin",
        username=username,
        extra={"password_hash": portal_hash},
    )
    client = app.test_client()
    response = client.get("/auth/sso?token=" + token)
    assert response.status_code == 302
    return client, firm_id, portal_firm_id, username


# Scenario 1: Portal login → Launch → auto-authenticated to tenant DB
def test_scenario1_sso_launch_authenticates_to_tenant_db(phase4e_env):
    client, firm_id, _, username = _provision_and_sso()

    with client.session_transaction() as sess:
        assert sess.get("user_id")
        assert sess.get("firm_id") == firm_id
        assert sess.get("sso_login") is True
        assert sess.get("username") == username

    response = client.get("/client-ledger")
    assert response.status_code == 200

    tenant_db = get_db_for_firm(firm_id)
    user = tenant_db.get_user_by_username(username)
    assert user is not None


# Scenario 2: /login redirects to Portal login
def test_scenario2_login_redirects_to_portal(phase4e_env):
    client = app.test_client()
    response = client.get("/login")
    assert response.status_code == 302
    assert response.location.startswith(get_portal_login_url().split("?")[0])
    assert response.location.startswith(f"{PORTAL_TEST_URL}/login")


def test_scenario2_login_post_redirects_to_portal(phase4e_env):
    client = app.test_client()
    response = client.post(
        "/login",
        data={"username": "someone", "password": "secret"},
    )
    assert response.status_code == 302
    assert f"{PORTAL_TEST_URL}/login" in response.location
    assert "direct_login_disabled" in response.location


def test_scenario2_login_clears_stale_session_keys(phase4e_env):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["firm_id"] = "stale-firm"

    response = client.get("/login")
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert "firm_id" not in sess


# Scenario 3: Ledger logout redirects to Portal dashboard
def test_scenario3_logout_redirects_to_portal_dashboard(phase4e_env):
    client, _, _, _ = _provision_and_sso()
    response = client.get("/logout")
    assert response.status_code == 302
    assert response.location == get_portal_dashboard_url()
    assert response.location == f"{PORTAL_TEST_URL}/portal"

    with client.session_transaction() as sess:
        assert not sess.get("user_id")


def test_scenario3_sso_logout_redirects_to_portal(phase4e_env):
    client, _, _, _ = _provision_and_sso()
    response = client.get("/auth/sso/logout")
    assert response.status_code == 302
    assert response.location == f"{PORTAL_TEST_URL}/portal"


# Scenario 4: Legacy auth routes redirect to Portal (no Ledger auth UI)
def test_scenario4_legacy_auth_routes_redirect_to_portal_login(phase4e_env):
    client = app.test_client()
    login_base = get_portal_login_url().split("?")[0]

    for path, method, data in [
        ("/admin/recovery", "GET", None),
        ("/admin/recovery", "POST", {"username": "x", "recovery_key": "SRN-A-B-C"}),
        ("/admin/recovery/reset", "GET", None),
        ("/admin-reset-password/fake-token", "GET", None),
        ("/reset-password/fake-token", "GET", None),
        ("/force-password-change", "GET", None),
    ]:
        if method == "GET":
            response = client.get(path)
        else:
            response = client.post(path, data=data)
        assert response.status_code == 302, path
        assert response.location.startswith(login_base)


def test_scenario4_force_password_change_clears_session(phase4e_env):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 99
        sess["sso_login"] = True
    response = client.get("/force-password-change")
    assert response.status_code == 302
    assert f"{PORTAL_TEST_URL}/login" in response.location
    assert "legacy_route" in response.location
    with client.session_transaction() as sess:
        assert not sess.get("user_id")


def test_security_page_is_portal_only(phase4e_env):
    client, _, _, _ = _provision_and_sso()
    response = client.get("/admin/security")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Authentication and account management are handled through the Nexal Legal Portal." in html
    assert "Open Portal" in html
    assert "Forgot" not in html
    assert "Recovery" not in html
    assert "Reset Password" not in html


def test_user_management_page_has_no_password_ui(phase4e_env):
    client, _, _, _ = _provision_and_sso()
    response = client.get("/user-management")
    assert response.status_code == 200
    html = response.data.decode()
    assert "User invitations and account management are handled through the Nexal Legal Portal." in html
    assert "Manage Users in Portal" in html
    assert "temp_password" not in html
    assert "Reset Password" not in html
    assert "recovery" not in html.lower()


# Scenario 5: Invited user architecture — Ledger rejects local user creation
def test_scenario5_add_user_redirects_with_portal_message(phase4e_env):
    client, _, _, _ = _provision_and_sso()
    response = client.post(
        "/user-management/add-user",
        data={
            "name": "New User",
            "username": "newuser",
            "role": "staff",
            "temp_password": "temp123",
        },
    )
    assert response.status_code == 302
    assert "user-management" in response.location


# Scenario 6: Multi-firm isolation on SSO launch
def test_scenario6_multi_firm_sso_isolation(phase4e_env):
    portal_a = str(uuid.uuid4())
    portal_b = str(uuid.uuid4())

    client_a, firm_a, _, user_a = _provision_and_sso(portal_firm_id=portal_a, username="user_a")
    with client_a.session_transaction() as sess:
        assert sess["firm_id"] == firm_a

    client_b, firm_b, _, user_b = _provision_and_sso(portal_firm_id=portal_b, username="user_b")
    with client_b.session_transaction() as sess:
        assert sess["firm_id"] == firm_b

    assert firm_a != firm_b
    db_a = get_db_for_firm(firm_a)
    db_b = get_db_for_firm(firm_b)
    assert db_a.db_path != db_b.db_path
    assert db_a.get_user_by_username(user_a) is not None
    assert db_a.get_user_by_username(user_b) is None
    assert db_b.get_user_by_username(user_b) is not None
    assert db_b.get_user_by_username(user_a) is None


def test_unauthenticated_access_redirects_to_portal(phase4e_env):
    client = app.test_client()
    response = client.get("/client-ledger")
    assert response.status_code == 302
    assert f"{PORTAL_TEST_URL}/login" in response.location


def test_non_sso_session_rejected(phase4e_env):
    """Direct sessions without sso_login flag must not access Ledger."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "legacy"
        sess["role"] = "admin"

    response = client.get("/client-ledger")
    assert response.status_code == 302
    assert f"{PORTAL_TEST_URL}/login" in response.location
    assert "sso_required" in response.location

    with client.session_transaction() as sess:
        assert not sess.get("user_id")
