"""Subscription package limits, system user exclusion, and package display."""
import os
import tempfile
import uuid

import pytest
from werkzeug.security import generate_password_hash

from database import Database
from lib.firm_package import (
    check_user_limit,
    package_usage_summary,
    resolve_firm_tier,
    sync_subscription_from_portal,
)
from lib.subscription_packages import (
    max_users_for_tier,
    normalize_tier,
    package_display_label,
    user_limit_message,
)
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.provision import provision_firm


@pytest.fixture()
def isolated_data_root(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-packages")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        yield root
        from db_router import reset_router

        reset_router()


def test_package_labels_and_limits():
    assert normalize_tier("Professional") == "professional"
    assert max_users_for_tier("essential") == 2
    assert max_users_for_tier("professional") == 5
    assert max_users_for_tier("practice_plus") == 10
    assert package_display_label("essential") == "Essential (£39/month)"
    assert "maximum of 2 users" in user_limit_message("essential")


def test_system_users_excluded_from_billable_counts():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "ledger.db")
        db = Database(db_path=db_path)
        db.create_user("portal.admin", generate_password_hash("x"), "admin", temporary=False)
        db.create_user("staff.user", generate_password_hash("x"), "staff", temporary=False)

        assert db.count_billable_active_users() == 2
        billable = {u["username"] for u in db.get_billable_active_users()}
        assert "admin" not in billable
        assert "staff" not in billable
        assert "portal.admin" in billable


def test_essential_blocks_third_billable_user():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "ledger.db")
        db = Database(db_path=db_path)
        db.set_config("firm_subscription_tier", "essential")
        db.create_user("owner", generate_password_hash("x"), "admin", temporary=False)
        db.create_user("assistant", generate_password_hash("x"), "staff", temporary=False)

        session = {}
        assert check_user_limit(db, session) is not None
        usage = package_usage_summary(db, session)
        assert usage["at_limit"] is True
        assert usage["active_users"] == 2


def test_provisioned_firm_has_no_template_users(isolated_data_root):
    slug = f"pkg-firm-{uuid.uuid4().hex[:8]}"
    result = provision_firm(
        name="Package Test Firm",
        slug=slug,
        subscription_tier="essential",
    )
    tenant_db = Database(db_path=result["database_path"])
    assert tenant_db.count_billable_active_users() == 0
    usernames = {u["username"] for u in tenant_db.get_all_users_unfiltered()}
    assert usernames == set()
    assert result["firm"]["subscription_tier"] == "essential"


def test_provisioned_firm_stores_package_tier(isolated_data_root):
    slug = f"tier-firm-{uuid.uuid4().hex[:8]}"
    result = provision_firm(
        name="Tier Firm",
        slug=slug,
        subscription_tier="professional",
    )
    tenant_db = Database(db_path=result["database_path"])
    assert tenant_db.get_config("firm_subscription_tier") == "professional"
    assert resolve_firm_tier({"firm_id": result["firm"]["id"]}, tenant_db) == "professional"


def test_sync_subscription_from_portal_updates_platform_and_tenant(isolated_data_root):
    slug = f"sync-firm-{uuid.uuid4().hex[:8]}"
    result = provision_firm(name="Sync Firm", slug=slug, subscription_tier="essential")
    firm_id = result["firm"]["id"]

    from db_router import get_db_for_firm, reset_router

    reset_router()
    tier = sync_subscription_from_portal(firm_id, "practice_plus")
    assert tier == "practice_plus"

    platform = PlatformDatabase()
    assert platform.get_firm(firm_id)["subscription_tier"] == "practice_plus"
    assert get_db_for_firm(firm_id).get_config("firm_subscription_tier") == "practice_plus"


def test_create_user_respects_package_limit_via_database_layer():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "ledger.db")
        db = Database(db_path=db_path)
        db.set_config("firm_subscription_tier", "essential")
        db.create_user("one", generate_password_hash("x"), "admin")
        db.create_user("two", generate_password_hash("x"), "staff")
        assert check_user_limit(db, {}) == user_limit_message("essential")
