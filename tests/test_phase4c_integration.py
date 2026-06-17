"""Phase 4C — end-to-end portal ↔ ledger integration validation."""
import os
import tempfile
import uuid

import pytest

from db_router import get_db_for_firm, reset_router
from lib.permissions import (
    can_access_admin_functions,
    can_access_client_operations,
    can_access_financial_functions,
    can_modify_ledger_data,
    is_read_only_user,
)
from nexal_platform.data_integrity import audit_platform_integrity
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.portal_link import ensure_portal_firm_linked, resolve_active_portal_firm
from nexal_platform.provision import provision_firm
from nexal_platform.session_security import safe_redirect_target, validate_sso_session_binding
from portal_bridge import ensure_portal_user_in_ledger, establish_sso_session, resolve_platform_firm
from sso_auth import generate_sso_token, map_portal_role_to_ledger, validate_sso_token


@pytest.fixture()
def phase4c_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-phase4c")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "phase4c-test-secret-key")
        reset_router()
        yield root
        reset_router()


def _provision_portal_linked_firm(
    portal_firm_id: str,
    name: str,
    owner_email: str,
    portal_user_id: str,
):
    slug = "firm-" + portal_firm_id.replace("-", "")[:10]
    return provision_firm(
        name=name,
        slug=slug,
        portal_firm_id=portal_firm_id,
        owner_email=owner_email,
        portal_user_id=portal_user_id,
    )


def _sso_token(
    portal_user_id: str,
    email: str,
    portal_firm_id: str,
    role: str = "firm_admin",
    firm_name: str = "Test Firm",
):
    return generate_sso_token(
        user_id=portal_user_id,
        email=email,
        firm_id=portal_firm_id,
        role=role,
        username=email.split("@")[0],
        extra={"firm_name": firm_name},
    )


# ── Section 2: Multi-tenant testing ───────────────────────────────────────────


def test_firm_a_and_b_create_isolated_clients_users_and_ledger_entries(phase4c_env):
    portal_a = str(uuid.uuid4())
    portal_b = str(uuid.uuid4())
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())

    firm_a = _provision_portal_linked_firm(portal_a, "Firm Alpha", "alpha@example.com", user_a)
    firm_b = _provision_portal_linked_firm(portal_b, "Firm Beta", "beta@example.com", user_b)
    db_a = get_db_for_firm(firm_a["firm"]["id"])
    db_b = get_db_for_firm(firm_b["firm"]["id"])

    conn_a = db_a.get_connection()
    conn_b = db_b.get_connection()
    try:
        conn_a.execute(
            "INSERT INTO clients (client_code, client_name) VALUES ('A-CLIENT', 'Alpha Client')"
        )
        conn_b.execute(
            "INSERT INTO clients (client_code, client_name) VALUES ('B-CLIENT', 'Beta Client')"
        )
        conn_a.execute(
            """
            INSERT INTO users (username, password_hash, role, active, portal_user_id, email, firm_id)
            VALUES ('alpha-user', 'hash', 'staff', 1, ?, 'alpha@example.com', ?)
            """,
            (user_a, firm_a["firm"]["id"]),
        )
        conn_b.execute(
            """
            INSERT INTO users (username, password_hash, role, active, portal_user_id, email, firm_id)
            VALUES ('beta-user', 'hash', 'staff', 1, ?, 'beta@example.com', ?)
            """,
            (user_b, firm_b["firm"]["id"]),
        )
        conn_a.commit()
        conn_b.commit()

        a_clients = [r[0] for r in conn_a.execute("SELECT client_code FROM clients").fetchall()]
        b_clients = [r[0] for r in conn_b.execute("SELECT client_code FROM clients").fetchall()]
        a_users = [r[0] for r in conn_a.execute("SELECT username FROM users").fetchall()]
        b_users = [r[0] for r in conn_b.execute("SELECT username FROM users").fetchall()]
    finally:
        conn_a.close()
        conn_b.close()

    assert "A-CLIENT" in a_clients
    assert "B-CLIENT" in b_clients
    assert "B-CLIENT" not in a_clients
    assert "A-CLIENT" not in b_clients
    assert "alpha-user" in a_users
    assert "beta-user" in b_users
    assert firm_a["database_path"] != firm_b["database_path"]


def test_direct_database_routing_cannot_cross_tenants(phase4c_env):
    portal_a = str(uuid.uuid4())
    portal_b = str(uuid.uuid4())
    firm_a = _provision_portal_linked_firm(portal_a, "Route A", "routea@example.com", str(uuid.uuid4()))
    firm_b = _provision_portal_linked_firm(portal_b, "Route B", "routeb@example.com", str(uuid.uuid4()))

    db_a = get_db_for_firm(firm_a["firm"]["id"])
    conn = db_a.get_connection()
    try:
        conn.execute(
            "INSERT INTO clients (client_code, client_name) VALUES ('ROUTE-A', 'Route A Client')"
        )
        conn.commit()
    finally:
        conn.close()

    db_b = get_db_for_firm(firm_b["firm"]["id"])
    conn_b = db_b.get_connection()
    try:
        codes = [r[0] for r in conn_b.execute("SELECT client_code FROM clients").fetchall()]
    finally:
        conn_b.close()

    assert "ROUTE-A" not in codes


def test_portal_launch_resolves_correct_tenant_each_time(phase4c_env):
    from app import app

    portal_a = str(uuid.uuid4())
    portal_b = str(uuid.uuid4())
    _provision_portal_linked_firm(portal_a, "Launch A", "launcha@example.com", str(uuid.uuid4()))
    _provision_portal_linked_firm(portal_b, "Launch B", "launchb@example.com", str(uuid.uuid4()))

    client = app.test_client()
    for portal_id, email in (
        (portal_a, "launcha@example.com"),
        (portal_b, "launchb@example.com"),
    ):
        token = _sso_token(str(uuid.uuid4()), email, portal_id)
        response = client.get("/auth/sso?token=" + token)
        assert response.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("firm_id") == resolve_platform_firm(portal_id)["id"]


# ── Section 3: SSO validation ─────────────────────────────────────────────────


def test_sso_valid_token_end_to_end_session_and_dashboard(phase4c_env):
    from app import app

    portal_firm_id = str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    token = _sso_token(portal_user_id, "owner@sso.example", portal_firm_id, firm_name="SSO Firm")

    client = app.test_client()
    response = client.get("/auth/sso?token=" + token)
    assert response.status_code == 302

    with client.session_transaction() as sess:
        assert sess.get("sso_login") is True
        assert sess.get("user_id") is not None
        assert sess.get("firm_id") is not None

    dashboard = client.get("/client-ledger")
    assert dashboard.status_code == 200


def test_sso_expired_token_rejected(phase4c_env, monkeypatch):
    import sso_auth

    monkeypatch.setattr(sso_auth, "SSO_TOKEN_TTL", -30)
    token = _sso_token(str(uuid.uuid4()), "expired@example.com", str(uuid.uuid4()))
    with pytest.raises(ValueError, match="expired"):
        validate_sso_token(token)


def test_sso_invalid_and_tampered_tokens_rejected(phase4c_env):
    token = _sso_token(str(uuid.uuid4()), "valid@example.com", str(uuid.uuid4()))
    with pytest.raises(ValueError):
        validate_sso_token(token + "tampered")
    with pytest.raises(ValueError):
        validate_sso_token("not.a.jwt")


def test_sso_auto_provisions_missing_firm(phase4c_env):
    portal_firm_id = str(uuid.uuid4())
    firm = ensure_portal_firm_linked(
        portal_firm_id,
        {
            "sub": str(uuid.uuid4()),
            "email": "newfirm@example.com",
            "firm_name": "Brand New Firm",
            "role": "firm_admin",
        },
    )
    assert firm["portal_firm_id"] == portal_firm_id
    assert resolve_active_portal_firm(portal_firm_id)["id"] == firm["id"]


def test_sso_existing_firm_launch_reuses_tenant(phase4c_env):
    portal_firm_id = str(uuid.uuid4())
    first = _provision_portal_linked_firm(
        portal_firm_id,
        "Existing Firm",
        "existing@example.com",
        str(uuid.uuid4()),
    )
    second = resolve_active_portal_firm(portal_firm_id)
    assert second["id"] == first["firm"]["id"]


def test_sso_open_redirect_blocked(phase4c_env):
    from app import app

    token = _sso_token(str(uuid.uuid4()), "redirect@example.com", str(uuid.uuid4()))
    client = app.test_client()
    response = client.get("/auth/sso?token=" + token + "&next=https://evil.example/phish")
    assert response.status_code == 302
    assert "evil.example" not in response.headers["Location"]


# ── Section 4: User & role testing ────────────────────────────────────────────


@pytest.mark.parametrize(
    "portal_role,ledger_role,can_modify,can_admin,can_finance,can_clients",
    [
        ("firm_admin", "admin", True, True, True, True),
        ("staff", "staff", True, False, True, True),
        ("cashier", "staff", True, False, True, True),
    ],
)
def test_portal_role_permission_boundaries(
    phase4c_env,
    portal_role,
    ledger_role,
    can_modify,
    can_admin,
    can_finance,
    can_clients,
):
    from app import app

    with app.test_request_context():
        from flask import session

        session["role"] = ledger_role
        session["portal_role"] = portal_role
        assert can_modify_ledger_data() is can_modify
        assert can_access_admin_functions() is can_admin
        assert can_access_financial_functions() is can_finance
        assert can_access_client_operations() is can_clients
        assert is_read_only_user() is False


def test_role_mapping_for_portal_roles(phase4c_env):
    assert map_portal_role_to_ledger("firm_admin") == "admin"
    assert map_portal_role_to_ledger("cashier") == "staff"
    assert map_portal_role_to_ledger("staff") == "staff"
    assert map_portal_role_to_ledger("read_only") == "staff"


# ── Section 5: Ledger functional testing ──────────────────────────────────────


def test_client_creation_and_balance_posting(phase4c_env):
    portal_firm_id = str(uuid.uuid4())
    result = _provision_portal_linked_firm(
        portal_firm_id,
        "Ledger Functional Firm",
        "ledger@example.com",
        str(uuid.uuid4()),
    )
    db = get_db_for_firm(result["firm"]["id"])
    conn = db.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO clients (client_code, client_name, matter_reference, description)
            VALUES ('LF-001', 'Functional Client', 'MAT-100', 'Phase 4C test')
            """
        )
        client_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO ledger_transactions (
                client_id, transaction_date, amount, transaction_type,
                reference, source, description, created_by
            ) VALUES (?, '2026-06-01', 100.00, 'Receipt', 'REF-001', 'Cash', 'Client receipt', 'tester')
            """,
            (client_id,),
        )
        conn.commit()
        balance = conn.execute(
            """
            SELECT amount FROM ledger_transactions
            WHERE client_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (client_id,),
        ).fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()

    assert float(balance) == 100.0
    assert audit_count >= 0


# ── Section 6: Session & security testing ─────────────────────────────────────


def test_sso_session_binding_rejects_tampered_firm_id(phase4c_env):
    portal_firm_id = str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    provisioned = _provision_portal_linked_firm(
        portal_firm_id,
        "Binding Firm",
        "binding@example.com",
        portal_user_id,
    )
    user = ensure_portal_user_in_ledger(
        {
            "sub": portal_user_id,
            "email": "binding@example.com",
            "role": "firm_admin",
        },
        provisioned["firm"]["id"],
    )

    valid_session = {
        "sso_login": True,
        "firm_id": provisioned["firm"]["id"],
        "user_id": user["user_id"],
        "portal_user_id": portal_user_id,
    }
    assert validate_sso_session_binding(valid_session, get_db_for_firm) is None

    tampered = dict(valid_session)
    tampered["firm_id"] = str(uuid.uuid4())
    assert validate_sso_session_binding(tampered, get_db_for_firm) is not None


def test_sso_logout_clears_session(phase4c_env):
    from app import app

    token = _sso_token(str(uuid.uuid4()), "logout@example.com", str(uuid.uuid4()))
    client = app.test_client()
    client.get("/auth/sso?token=" + token)
    response = client.get("/auth/sso/logout")
    assert response.status_code == 302
    status = client.get("/auth/sso/status")
    assert status.get_json()["authenticated"] is False


def test_safe_redirect_rejects_external_urls(phase4c_env):
    from app import app

    with app.test_request_context():
        assert safe_redirect_target("/client-ledger").endswith("/client-ledger")
        assert "evil" not in safe_redirect_target("https://evil.example")
        assert safe_redirect_target("//evil.example").endswith("/client-ledger")


def test_email_linking_does_not_hijack_existing_portal_user(phase4c_env):
    portal_firm_id = str(uuid.uuid4())
    provisioned = _provision_portal_linked_firm(
        portal_firm_id,
        "Email Firm",
        "owner@email.example",
        str(uuid.uuid4()),
    )
    platform_firm_id = provisioned["firm"]["id"]
    db = get_db_for_firm(platform_firm_id)
    conn = db.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, active, portal_user_id, email, firm_id)
            VALUES ('linked-user', 'hash', 'staff', 1, 'existing-portal-user', 'shared@email.example', ?)
            """,
            (platform_firm_id,),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(LookupError):
        ensure_portal_user_in_ledger(
            {
                "sub": "different-portal-user",
                "email": "shared@email.example",
                "role": "firm_admin",
            },
            platform_firm_id,
        )


# ── Section 7: Data integrity testing ─────────────────────────────────────────


def test_platform_integrity_after_provisioning(phase4c_env):
    for idx in range(2):
        _provision_portal_linked_firm(
            str(uuid.uuid4()),
            f"Integrity Firm {idx}",
            f"integrity{idx}@example.com",
            str(uuid.uuid4()),
        )

    report = audit_platform_integrity()
    assert report["passed"] is True
    assert report["firm_count"] == 2
    assert report["linked_portal_firms"] == 2
    assert report["findings"] == []


def test_portal_firm_id_unique_constraint(phase4c_env):
    portal_firm_id = str(uuid.uuid4())
    _provision_portal_linked_firm(
        portal_firm_id,
        "Unique Firm",
        "unique@example.com",
        str(uuid.uuid4()),
    )
    platform = PlatformDatabase()
    with pytest.raises(Exception):
        platform.create_firm(
            name="Duplicate Link",
            slug="duplicate-link-firm",
            portal_firm_id=portal_firm_id,
        )


def test_sso_session_clears_override_state_on_login(phase4c_env):
    from app import app

    token = _sso_token(str(uuid.uuid4()), "clear@example.com", str(uuid.uuid4()))
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["override_mode"] = True
        sess["reserved_client_code"] = "OLD"
    response = client.get("/auth/sso?token=" + token)
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("override_mode") is not True
        assert sess.get("reserved_client_code") is None
