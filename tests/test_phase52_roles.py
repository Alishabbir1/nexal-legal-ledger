"""Phase 5.3 portal role mapping tests for Nexal Legal Ledger."""
import pytest

from portal_bridge import ROLE_MAP
from sso_auth import map_portal_role_to_ledger


@pytest.mark.parametrize(
    "portal_role,ledger_role",
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
def test_map_portal_role_to_ledger(portal_role, ledger_role):
    assert map_portal_role_to_ledger(portal_role) == ledger_role


def test_role_map_admin_staff_only():
    assert ROLE_MAP["firm_admin"] == "admin"
    assert ROLE_MAP["staff"] == "staff"
    assert ROLE_MAP["cashier"] == "staff"
