"""Phase 5.3 ledger permission audit tests — Admin / Staff only."""
import pytest

from lib.permissions import (
    can_access_admin_functions,
    can_access_client_operations,
    can_access_financial_functions,
    can_access_reports,
    can_edit_client_details,
    can_modify_ledger_data,
    is_read_only_user,
    normalize_portal_role,
)


@pytest.mark.parametrize(
    "legacy_role,expected",
    [
        ("firm_admin", "admin"),
        ("admin", "admin"),
        ("owner", "admin"),
        ("practice_manager", "admin"),
        ("manager", "admin"),
        ("staff", "staff"),
        ("cashier", "staff"),
        ("fee_earner", "staff"),
        ("read_only", "staff"),
    ],
)
def test_normalize_portal_role(legacy_role, expected):
    assert normalize_portal_role(legacy_role) == expected


@pytest.mark.parametrize(
    "portal_role,ledger_role,modify,admin,finance,clients,reports,edit_clients",
    [
        ("firm_admin", "admin", True, True, True, True, True, True),
        ("staff", "staff", True, False, True, True, True, True),
        ("read_only", "staff", False, False, True, True, True, False),
    ],
)
def test_phase53_ledger_permission_matrix(
    portal_role,
    ledger_role,
    modify,
    admin,
    finance,
    clients,
    reports,
    edit_clients,
):
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = "phase53-test-secret"
    with app.test_request_context():
        from flask import session

        session["portal_role"] = portal_role
        session["role"] = ledger_role
        assert can_modify_ledger_data() is modify
        assert can_access_admin_functions() is admin
        assert can_access_financial_functions() is finance
        assert can_access_client_operations() is clients
        assert can_access_reports() is reports
        assert can_edit_client_details() is edit_clients
        assert is_read_only_user() is (portal_role == "read_only")


def test_staff_cannot_access_ledger_admin():
    assert can_access_admin_functions(role="staff", portal_role="staff") is False


def test_legacy_cashier_maps_to_staff_permissions():
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = "phase53-test-secret"
    with app.test_request_context():
        from flask import session

        session["portal_role"] = "cashier"
        session["role"] = "staff"
        assert can_access_admin_functions() is False
        assert can_access_financial_functions() is True
