"""Phase 5.2 portal role mapping tests for Nexal Legal Ledger."""
import pytest

from portal_bridge import ROLE_MAP
from sso_auth import map_portal_role_to_ledger


@pytest.mark.parametrize(
    "portal_role,ledger_role",
    [
        ("firm_admin", "admin"),
        ("admin", "admin"),
        ("manager", "admin"),
        ("cashier", "staff"),
        ("fee_earner", "staff"),
        ("read_only", "staff"),
        ("staff", "staff"),
    ],
)
def test_map_portal_role_to_ledger(portal_role, ledger_role):
    assert map_portal_role_to_ledger(portal_role) == ledger_role


def test_role_map_includes_phase52_roles():
    assert ROLE_MAP["manager"] == "admin"
    assert ROLE_MAP["fee_earner"] == "staff"
