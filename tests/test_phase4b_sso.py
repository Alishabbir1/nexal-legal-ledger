"""Phase 4B SSO and portal integration tests for Nexal Legal Ledger."""
import os
import tempfile
import uuid

import pytest

from db_router import get_db_for_firm
from nexal_platform.provision import provision_firm
from portal_bridge import ensure_portal_user_in_ledger, provision_portal_user, resolve_platform_firm
from sso_auth import (
    generate_sso_token,
    is_token_valid,
    map_portal_role_to_ledger,
    validate_sso_token,
)


@pytest.fixture()
def phase4b_env(monkeypatch):
    from db_router import reset_router

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-phase4b")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "phase4b-test-secret-key")
        reset_router()
        yield root
        reset_router()


def _provision_linked_firm(firm_code: str, name: str, portal_firm_id: str):
    slug = firm_code.lower().replace("_", "-")
    return provision_firm(
        name=name,
        slug=slug,
        firm_code=firm_code,
        portal_firm_id=portal_firm_id,
        owner_email=f"admin@{slug}.example",
    )


def test_generate_and_validate_sso_token(phase4b_env):
    token = generate_sso_token(
        user_id="portal-user-1",
        email="owner@alpha.example",
        firm_id="portal-firm-1",
        role="firm_admin",
        username="owner",
    )
    assert is_token_valid(token)
    payload = validate_sso_token(token)
    assert payload["sub"] == "portal-user-1"
    assert payload["firm_id"] == "portal-firm-1"
    assert payload["role"] == "firm_admin"


def test_invalid_token_rejected(phase4b_env):
    token = generate_sso_token("u1", "a@b.example", "firm-1", role="staff")
    assert not is_token_valid(token + "x")


def test_firm_routing_for_provisioned_firms(phase4b_env):
    firms = [
        _provision_linked_firm("FIRM001", "Alpha Law LLP", "portal-firm-001"),
        _provision_linked_firm("FIRM002", "Beta Solicitors Ltd", "portal-firm-002"),
        _provision_linked_firm("FIRM003", "Gamma Legal", "portal-firm-003"),
    ]
    paths = [get_db_for_firm(item["firm"]["id"]).db_path for item in firms]
    assert len(set(paths)) == 3


def test_user_provisioning_and_mapping(phase4b_env):
    result = _provision_linked_firm("FIRM010", "Test Firm", "portal-firm-010")
    platform_firm_id = result["firm"]["id"]
    payload = {
        "sub": "portal-user-010",
        "email": "owner@testfirm.example",
        "firm_id": "portal-firm-010",
        "role": "firm_admin",
        "username": "owner",
    }
    user = ensure_portal_user_in_ledger(payload, platform_firm_id)
    assert user["portal_user_id"] == "portal-user-010"
    assert user["email"] == "owner@testfirm.example"
    assert map_portal_role_to_ledger("firm_admin") == "admin"


def test_tenant_isolation_between_firms(phase4b_env):
    firm_a = _provision_linked_firm("FIRM011", "Firm A", "portal-firm-011")
    firm_b = _provision_linked_firm("FIRM012", "Firm B", "portal-firm-012")
    db_a = get_db_for_firm(firm_a["firm"]["id"])
    db_b = get_db_for_firm(firm_b["firm"]["id"])

    conn_a = db_a.get_connection()
    conn_b = db_b.get_connection()
    try:
        conn_a.execute(
            "INSERT INTO clients (client_code, client_name) VALUES ('ONLY-A', 'Only A')"
        )
        conn_b.execute(
            "INSERT INTO clients (client_code, client_name) VALUES ('ONLY-B', 'Only B')"
        )
        conn_a.commit()
        conn_b.commit()

        a_codes = [r[0] for r in conn_a.execute("SELECT client_code FROM clients").fetchall()]
        b_codes = [r[0] for r in conn_b.execute("SELECT client_code FROM clients").fetchall()]
    finally:
        conn_a.close()
        conn_b.close()

    assert "ONLY-A" in a_codes
    assert "ONLY-B" in b_codes
    assert "ONLY-B" not in a_codes
    assert "ONLY-A" not in b_codes


def test_resolve_platform_firm_by_portal_id(phase4b_env):
    result = _provision_linked_firm("FIRM013", "Portal Map Firm", "portal-firm-013")
    firm = resolve_platform_firm("portal-firm-013")
    assert firm["id"] == result["firm"]["id"]


def test_flask_sso_login_endpoint(phase4b_env):
    from db_router import reset_router
    reset_router()
    from app import app

    result = _provision_linked_firm("FIRM014", "SSO Endpoint Firm", "portal-firm-014")
    platform_firm_id = result["firm"]["id"]
    token = generate_sso_token(
        user_id="portal-user-014",
        email="sso@endpoint.example",
        firm_id="portal-firm-014",
        role="firm_admin",
        username="sso",
    )

    client = app.test_client()
    response = client.post("/api/sso-login", json={"token": token})
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["firm_id"] == platform_firm_id

    with client.session_transaction() as sess:
        assert sess.get("sso_login") is True
        assert sess.get("firm_id") == platform_firm_id
        assert sess.get("user_id") is not None

    status = client.get("/auth/sso/status")
    assert status.get_json()["authenticated"] is True


def test_expired_token_rejected(phase4b_env, monkeypatch):
    import sso_auth

    monkeypatch.setattr(sso_auth, "SSO_TOKEN_TTL", -10)
    token = generate_sso_token("u1", "expired@example.com", "portal-firm-x", role="staff")
    with pytest.raises(ValueError, match="expired"):
        validate_sso_token(token)
