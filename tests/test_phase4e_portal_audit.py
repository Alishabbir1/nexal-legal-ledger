"""Phase 4E final verification — Portal links and package enforcement audit."""
import os
import tempfile
import uuid

import bcrypt
import pytest

from app import app
from db_router import get_db_for_firm, reset_router
from lib.portal_auth import (
    DEFAULT_PORTAL_URL,
    get_portal_base_url,
    get_portal_dashboard_url,
    get_portal_login_url,
    get_portal_logout_url,
    get_portal_users_url,
)
from lib.subscription_packages import (
    max_users_for_tier,
    package_display_label,
)
from nexal_platform.provision import provision_firm
from sso_auth import generate_sso_token

PORTAL_TEST_URL = "http://portal.test"


@pytest.fixture()
def audit_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-portal-audit")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "portal-audit-secret")
        monkeypatch.setenv("NEXAL_PORTAL_URL", PORTAL_TEST_URL)
        reset_router()
        yield root
        reset_router()


def test_portal_urls_use_nexal_portal_url_env(audit_env):
    assert get_portal_base_url() == PORTAL_TEST_URL
    assert get_portal_dashboard_url() == f"{PORTAL_TEST_URL}/portal"
    assert get_portal_users_url() == f"{PORTAL_TEST_URL}/portal/users"
    assert get_portal_login_url().startswith(f"{PORTAL_TEST_URL}/login")
    assert get_portal_logout_url() == f"{PORTAL_TEST_URL}/"


def test_default_portal_url_points_to_vercel_not_parked_domain():
    assert DEFAULT_PORTAL_URL == "https://nexal-legal.vercel.app"
    assert "nexallegal.co.uk/portal" not in DEFAULT_PORTAL_URL


def test_no_hardcoded_production_portal_urls_in_templates():
    template_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
    forbidden = ("https://nexallegal.co.uk", "localhost:3000", "forgot-password")
    for name in os.listdir(template_dir):
        if not name.endswith(".html"):
            continue
        path = os.path.join(template_dir, name)
        text = open(path, encoding="utf-8").read().lower()
        for token in forbidden:
            assert token not in text, f"{name} contains hardcoded {token}"


def test_open_portal_button_uses_dashboard_url(audit_env):
    portal_firm_id = str(uuid.uuid4())
    token = generate_sso_token(
        user_id=str(uuid.uuid4()),
        email="audit@example.com",
        firm_id=portal_firm_id,
        role="firm_admin",
        username="auditadmin",
        extra={"password_hash": bcrypt.hashpw(b"x", bcrypt.gensalt(12)).decode()},
    )
    provision_firm(
        name="Audit Firm",
        slug=f"audit-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
        subscription_tier="professional",
    )
    client = app.test_client()
    client.get("/auth/sso?token=" + token)
    response = client.get("/admin/security")
    html = response.data.decode()
    assert f'href="{get_portal_dashboard_url()}"' in html
    assert "Open Portal" in html


def test_manage_users_button_points_to_portal_users(audit_env):
    portal_firm_id = str(uuid.uuid4())
    token = generate_sso_token(
        user_id=str(uuid.uuid4()),
        email="users@example.com",
        firm_id=portal_firm_id,
        role="firm_admin",
        username="usersadmin",
        extra={"password_hash": bcrypt.hashpw(b"x", bcrypt.gensalt(12)).decode()},
    )
    provision_firm(
        name="Users Firm",
        slug=f"users-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )
    client = app.test_client()
    client.get("/auth/sso?token=" + token)
    response = client.get("/user-management")
    html = response.data.decode()
    assert f'href="{get_portal_users_url()}"' in html
    assert "Manage Users in Portal" in html


def test_package_badge_reflects_firm_tier(audit_env):
    portal_firm_id = str(uuid.uuid4())
    firm = provision_firm(
        name="Pro Firm",
        slug=f"pro-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
        subscription_tier="professional",
    )
    token = generate_sso_token(
        user_id=str(uuid.uuid4()),
        email="pro@example.com",
        firm_id=portal_firm_id,
        role="firm_admin",
        username="proadmin",
        extra={"password_hash": bcrypt.hashpw(b"x", bcrypt.gensalt(12)).decode()},
    )
    client = app.test_client()
    client.get("/auth/sso?token=" + token)
    response = client.get("/client-ledger")
    expected = package_display_label("professional")
    assert expected in response.data.decode()
    assert expected == "Professional (£79/month)"

    tenant_db = get_db_for_firm(firm["firm"]["id"])
    assert tenant_db.get_config("firm_subscription_tier") == "professional"


def test_package_limits_match_subscription_definitions():
    assert max_users_for_tier("essential") == 2
    assert max_users_for_tier("professional") == 5
    assert max_users_for_tier("practice_plus") == 10
    assert package_display_label("practice_plus") == "Practice Plus (£149/month)"


def test_logout_redirects_to_public_portal_not_dashboard_or_ledger_login(audit_env):
    portal_firm_id = str(uuid.uuid4())
    token = generate_sso_token(
        user_id=str(uuid.uuid4()),
        email="logout@example.com",
        firm_id=portal_firm_id,
        role="firm_admin",
        username="logoutadmin",
        extra={"password_hash": bcrypt.hashpw(b"x", bcrypt.gensalt(12)).decode()},
    )
    provision_firm(
        name="Logout Firm",
        slug=f"logout-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )
    client = app.test_client()
    client.get("/auth/sso?token=" + token)
    response = client.get("/logout")
    assert response.status_code == 302
    assert response.location == get_portal_logout_url()
    assert response.location.endswith("/")
    assert "/portal" not in response.location.replace(PORTAL_TEST_URL, "")
    assert "/login" not in response.location.replace(PORTAL_TEST_URL, "")
