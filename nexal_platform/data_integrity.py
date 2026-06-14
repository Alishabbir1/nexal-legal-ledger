"""
Phase 4C — platform.db integrity checks.
"""
from typing import Any, Dict, List

from nexal_platform.platform_db import PlatformDatabase


def audit_platform_integrity(platform: PlatformDatabase = None) -> Dict[str, Any]:
    """Return integrity findings for platform registry records."""
    platform = platform or PlatformDatabase()
    findings: List[str] = []
    firms = platform.list_firms()
    portal_ids = set()

    for firm in firms:
        portal_id = firm.get("portal_firm_id")
        if portal_id:
            if portal_id in portal_ids:
                findings.append("Duplicate portal_firm_id: " + portal_id)
            portal_ids.add(portal_id)

        try:
            workspace = platform.get_workspace_for_firm(firm["id"])
        except KeyError:
            findings.append("Missing workspace for firm: " + firm["id"])
            continue

        if workspace["status"] != "active" and firm["status"] == "active":
            findings.append("Active firm has non-active workspace: " + firm["id"])

        import os

        if not os.path.isfile(workspace["database_path"]):
            findings.append("Workspace database file missing: " + workspace["database_path"])

    return {
        "passed": len(findings) == 0,
        "firm_count": len(firms),
        "linked_portal_firms": len(portal_ids),
        "findings": findings,
    }
