"""Test stale portal_user_id relink and hijack protection."""
import os
import tempfile
import uuid

import pytest

from db_router import get_db_for_firm, reset_router
from nexal_platform.provision import provision_firm
from portal_bridge import ensure_portal_user_in_ledger


@pytest.fixture()
def phase4b_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-relink")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        monkeypatch.setenv("SSO_SECRET_KEY", "phase4b-test-secret-key")
        reset_router()
        yield root
        reset_router()


def _provision(portal_firm_id: str, owner_email: str, portal_user_id: str):
    slug = "relink-" + portal_firm_id[:8]
    return provision_firm(
        name="Relink Firm",
        slug=slug,
        portal_firm_id=portal_firm_id,
        owner_email=owner_email,
        portal_user_id=portal_user_id,
    )


def test_relinks_stale_customer_id_to_firm_user_id(phase4b_env):
    portal_firm_id = str(uuid.uuid4())
    customer_id = str(uuid.uuid4())
    firm_user_id = str(uuid.uuid4())
    email = "relink@example.com"

    provisioned = _provision(portal_firm_id, email, customer_id)
    db = get_db_for_firm(provisioned["firm"]["id"])
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO users (username, password_hash, role, active, portal_user_id, email, firm_id, temporary_password)
        VALUES ('relink-user', 'hash', 'admin', 1, ?, ?, ?, 0)
        """,
        (customer_id, email, provisioned["firm"]["id"]),
    )
    conn.commit()
    conn.close()

    user = ensure_portal_user_in_ledger(
        {
            "sub": firm_user_id,
            "email": email,
            "role": "firm_admin",
            "portal_customer_id": customer_id,
        },
        provisioned["firm"]["id"],
    )
    assert user["portal_user_id"] == firm_user_id


def test_email_hijack_still_blocked_with_portal_customer_id(phase4b_env):
    portal_firm_id = str(uuid.uuid4())
    provisioned = _provision(portal_firm_id, "owner@email.example", str(uuid.uuid4()))
    db = get_db_for_firm(provisioned["firm"]["id"])
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO users (username, password_hash, role, active, portal_user_id, email, firm_id, temporary_password)
        VALUES ('linked-user', 'hash', 'staff', 1, 'existing-portal-user', 'shared@email.example', ?, 0)
        """,
        (provisioned["firm"]["id"],),
    )
    conn.commit()
    conn.close()

    with pytest.raises(LookupError, match="conflict"):
        ensure_portal_user_in_ledger(
            {
                "sub": "different-portal-user",
                "email": "shared@email.example",
                "role": "firm_admin",
                "portal_customer_id": "unrelated-customer-id",
            },
            provisioned["firm"]["id"],
        )
