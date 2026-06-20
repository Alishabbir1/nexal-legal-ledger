"""Portal ↔ Ledger password hash synchronisation for SSO users."""
import os
import tempfile
import uuid

import bcrypt
import pytest
from werkzeug.security import generate_password_hash

from db_router import get_db_for_firm, reset_router
from lib.password_verification import verify_password
from lib.portal_password_sync import (
    force_sync_portal_password_hash,
    is_valid_password_hash,
    prepare_sso_password_for_verification,
    sync_portal_password_hash,
)
from nexal_platform.provision import provision_firm
from portal_bridge import ensure_portal_user_in_ledger
from sso_auth import generate_sso_token


@pytest.fixture()
def isolated_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-password-sync")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "password-sync-test-secret")
        reset_router()
        yield root
        reset_router()


def test_is_valid_password_hash_accepts_bcrypt_and_scrypt():
    assert is_valid_password_hash("$2b$12$abcdefghijklmnopqrstuv")
    assert is_valid_password_hash(generate_password_hash("x", method="scrypt"))
    assert not is_valid_password_hash("")
    assert not is_valid_password_hash("plaintext")


def test_sync_portal_password_hash_updates_ledger_user():
    with tempfile.TemporaryDirectory() as tmp:
        from database import Database

        db = Database(db_path=os.path.join(tmp, "ledger.db"))
        uid = db.create_user("owner", generate_password_hash("old"), "admin", temporary=False)
        portal_hash = generate_password_hash("PortalPass123!", method="scrypt")

        assert sync_portal_password_hash(db, uid, portal_hash) is True
        user = db.get_user_by_id(uid)
        assert user["password_hash"] == portal_hash
        assert verify_password(user["password_hash"], "PortalPass123!")

        assert sync_portal_password_hash(db, uid, portal_hash) is False


def test_sso_provision_stores_portal_password_hash(isolated_env):
    portal_firm_id = str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    portal_password = "MyPortalLogin99!"
    portal_hash = generate_password_hash(portal_password, method="scrypt")

    provision_firm(
        name="Password Sync Firm",
        slug=f"pwd-sync-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )

    payload = {
        "sub": portal_user_id,
        "email": "owner@password-sync.example",
        "role": "firm_admin",
        "username": "owner",
        "password_hash": portal_hash,
    }
    firm = __import__("nexal_platform.portal_link", fromlist=["resolve_active_portal_firm"]).resolve_active_portal_firm(
        portal_firm_id, payload
    )
    user = ensure_portal_user_in_ledger(payload, firm["id"])
    db = get_db_for_firm(firm["id"])
    ledger_user = db.get_user_by_id(user["user_id"])

    assert verify_password(ledger_user["password_hash"], portal_password)


def test_sso_login_resyncs_password_after_portal_change(isolated_env):
    portal_firm_id = str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    initial_hash = generate_password_hash("InitialPass1!", method="scrypt")
    updated_hash = generate_password_hash("UpdatedPass2!", method="scrypt")

    provision_firm(
        name="Resync Firm",
        slug=f"resync-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )

    base_payload = {
        "sub": portal_user_id,
        "email": "resync@example.com",
        "role": "firm_admin",
        "username": "resync",
    }
    firm = __import__("nexal_platform.portal_link", fromlist=["resolve_active_portal_firm"]).resolve_active_portal_firm(
        portal_firm_id, {**base_payload, "password_hash": initial_hash}
    )

    ensure_portal_user_in_ledger({**base_payload, "password_hash": initial_hash}, firm["id"])
    db = get_db_for_firm(firm["id"])
    user = db.get_user_by_username("resync")
    assert verify_password(user["password_hash"], "InitialPass1!")

    ensure_portal_user_in_ledger({**base_payload, "password_hash": updated_hash}, firm["id"])
    user = db.get_user_by_username("resync")
    assert verify_password(user["password_hash"], "UpdatedPass2!")


def test_sso_login_clears_recovery_confirm_lockout(isolated_env):
    """SSO re-launch clears legacy recovery-confirm lockout state."""
    from app import app

    portal_firm_id = str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    portal_password = "LockoutRepair99!"
    portal_hash = bcrypt.hashpw(
        portal_password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    provision_firm(
        name="Lockout Firm",
        slug=f"lockout-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )

    token = generate_sso_token(
        user_id=portal_user_id,
        email="lockout@example.com",
        firm_id=portal_firm_id,
        role="firm_admin",
        username="lockout",
        extra={"password_hash": portal_hash},
    )

    client = app.test_client()
    assert client.get("/auth/sso?token=" + token).status_code == 302

    with client.session_transaction() as sess:
        firm_id = sess["firm_id"]
        user_id = sess["user_id"]

    db = get_db_for_firm(firm_id)
    db.update_user_password(user_id, generate_password_hash("random-unknown", method="scrypt"))
    for _ in range(5):
        db.record_failed_recovery_confirm(user_id)
    locked, _, _ = db.is_recovery_confirm_locked(user_id)
    assert locked is True

    token2 = generate_sso_token(
        user_id=portal_user_id,
        email="lockout@example.com",
        firm_id=portal_firm_id,
        role="firm_admin",
        username="lockout",
        extra={"password_hash": portal_hash},
    )
    assert client.get("/auth/sso?token=" + token2).status_code == 302

    locked, _, _ = db.is_recovery_confirm_locked(user_id)
    assert locked is False
    user = db.get_user_by_id(user_id)
    assert verify_password(user["password_hash"], portal_password)


def test_prepare_sso_password_for_verification_repairs_from_session(isolated_env):
    portal_firm_id = str(uuid.uuid4())
    portal_user_id = str(uuid.uuid4())
    portal_password = "SessionRepair88!"
    portal_hash = bcrypt.hashpw(
        portal_password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    provision_firm(
        name="Session Repair Firm",
        slug=f"srepair-{uuid.uuid4().hex[:8]}",
        portal_firm_id=portal_firm_id,
    )
    payload = {
        "sub": portal_user_id,
        "email": "repair@example.com",
        "role": "firm_admin",
        "username": "repair",
        "password_hash": portal_hash,
    }
    firm = __import__(
        "nexal_platform.portal_link",
        fromlist=["resolve_active_portal_firm"],
    ).resolve_active_portal_firm(portal_firm_id, payload)
    user = ensure_portal_user_in_ledger(payload, firm["id"])
    db = get_db_for_firm(firm["id"])
    db.update_user_password(user["user_id"], generate_password_hash("wrong-stored", method="scrypt"))

    session = {
        "sso_login": True,
        "user_id": user["user_id"],
        "portal_password_hash": portal_hash,
    }
    prepare_sso_password_for_verification(db, session)
    ledger_user = db.get_user_by_id(user["user_id"])
    assert verify_password(ledger_user["password_hash"], portal_password)
