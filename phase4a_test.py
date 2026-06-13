#!/usr/bin/env python3
"""
Phase 4A validation suite for Nexal Legal.

Provisions FIRM001, FIRM002, FIRM003 and verifies routing, migration, and isolation.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from typing import Dict, List, Optional

from database import Database, _default_db_path

from db_router import TenantRouter
from nexal_platform.config import get_platform_paths
from nexal_platform.platform_db import PlatformDatabase
from nexal_platform.provision import provision_firm
from nexal_platform.template import ensure_template_database
from phase4a_migrate import migrate_legacy_database

TEST_FIRMS = [
    {
        "firm_code": "FIRM001",
        "name": "Alpha Law LLP",
        "slug": "alpha-law-llp",
        "owner_email": "admin@alpha-law.example",
        "marker_client": "ALPHA-CLIENT-001",
    },
    {
        "firm_code": "FIRM002",
        "name": "Beta Solicitors Ltd",
        "slug": "beta-solicitors-ltd",
        "owner_email": "admin@beta-solicitors.example",
        "marker_client": "BETA-CLIENT-002",
    },
    {
        "firm_code": "FIRM003",
        "name": "Gamma Legal",
        "slug": "gamma-legal",
        "owner_email": "admin@gamma-legal.example",
        "marker_client": "GAMMA-CLIENT-003",
    },
]


def _insert_marker(db: Database, client_code: str) -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO clients (client_code, client_name, matter_reference, description)
            VALUES (?, ?, ?, ?)
            """,
            (client_code, f"Client {client_code}", "MAT-001", "Phase 4A validation marker"),
        )
        conn.commit()
    finally:
        conn.close()


def _list_client_codes(db: Database) -> List[str]:
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT client_code FROM clients ORDER BY client_code").fetchall()
        return [row["client_code"] for row in rows]
    finally:
        conn.close()


def run_phase4a_tests(data_root: Optional[str] = None) -> Dict:
    """Execute the full Phase 4A validation suite."""
    results: List[Dict] = []
    passed = True

    root = data_root or tempfile.mkdtemp(prefix="nexal-phase4a-")
    os.environ["NEXAL_DATA_DIR"] = root

    paths = get_platform_paths(root)
    platform = PlatformDatabase(paths)

    # 1. Platform schema
    try:
        conn = platform.get_connection()
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        conn.close()
        ok = {"firms", "workspaces", "users"}.issubset(tables)
        results.append({"test": "platform_schema", "passed": ok, "tables": sorted(tables)})
        passed &= ok
    except Exception as exc:
        results.append({"test": "platform_schema", "passed": False, "error": str(exc)})
        passed = False

    # 2. Template database
    try:
        template_path = ensure_template_database(paths)
        ok = os.path.isfile(template_path) and template_path.endswith("solicitor_ledger.db")
        results.append({"test": "template_database", "passed": ok, "path": template_path})
        passed &= ok
    except Exception as exc:
        results.append({"test": "template_database", "passed": False, "error": str(exc)})
        passed = False

    # 3. Provisioning + workspace creation for test firms
    provisioned = []
    try:
        for firm_def in TEST_FIRMS:
            result = provision_firm(
                name=firm_def["name"],
                slug=firm_def["slug"],
                firm_code=firm_def["firm_code"],
                owner_email=firm_def["owner_email"],
                portal_user_id=f"portal-{firm_def['firm_code'].lower()}",
            )
            ok = (
                result["firm"]["firm_code"] == firm_def["firm_code"]
                and os.path.isfile(result["database_path"])
                and result["database_path"].endswith("solicitor_ledger.db")
                and result["workspace"]["database_path"] == result["database_path"]
                and result["platform_user"]["portal_user_id"] == f"portal-{firm_def['firm_code'].lower()}"
            )
            provisioned.append((firm_def, result))
            results.append(
                {
                    "test": f"provision_{firm_def['firm_code']}",
                    "passed": ok,
                    "database_path": result["database_path"],
                }
            )
            passed &= ok
    except Exception as exc:
        results.append({"test": "provisioning", "passed": False, "error": str(exc)})
        passed = False

    # 4. Routing
    router = TenantRouter(paths)
    try:
        for firm_def, prov in provisioned:
            firm, db = router.get_database_for_code(firm_def["firm_code"])
            ok = firm["id"] == prov["firm"]["id"] and db.db_path == prov["database_path"]
            results.append({"test": f"routing_{firm_def['firm_code']}", "passed": ok})
            passed &= ok
    except Exception as exc:
        results.append({"test": "routing", "passed": False, "error": str(exc)})
        passed = False

    # 5. Tenant isolation
    try:
        markers = {}
        for firm_def, prov in provisioned:
            db = router.get_database(prov["firm"]["id"])
            _insert_marker(db, firm_def["marker_client"])
            markers[firm_def["firm_code"]] = _list_client_codes(db)

        isolation_ok = True
        for firm_def, prov in provisioned:
            own = markers[firm_def["firm_code"]]
            if firm_def["marker_client"] not in own:
                isolation_ok = False
            for other_def, _ in provisioned:
                if other_def["firm_code"] == firm_def["firm_code"]:
                    continue
                if other_def["marker_client"] in own:
                    isolation_ok = False

        results.append({"test": "tenant_isolation", "passed": isolation_ok, "markers": markers})
        passed &= isolation_ok
    except Exception as exc:
        results.append({"test": "tenant_isolation", "passed": False, "error": str(exc)})
        passed = False

    # 6. Migration (non-destructive copy)
    try:
        legacy = _default_db_path()
        if os.path.isfile(legacy):
            migration = migrate_legacy_database(
                legacy_path=legacy,
                firm_name="Migration Test Firm",
                firm_code="FIRM999",
                slug="migration-test",
                move=False,
            )
            ok = migration["status"] in {"migrated", "already_migrated"}
            if migration["status"] == "migrated":
                ok &= os.path.isfile(legacy)
                ok &= os.path.isfile(migration["tenant_database_path"])
            results.append({"test": "migration", "passed": ok, "status": migration["status"]})
            passed &= ok
        else:
            results.append(
                {
                    "test": "migration",
                    "passed": True,
                    "skipped": True,
                    "reason": "No legacy database present",
                }
            )
    except Exception as exc:
        results.append({"test": "migration", "passed": False, "error": str(exc)})
        passed = False

    # 7. Backwards compatibility — legacy Database() singleton path still works
    try:
        legacy_db = Database()
        conn = legacy_db.get_connection()
        conn.execute("SELECT 1")
        conn.close()
        results.append(
            {
                "test": "backwards_compatibility",
                "passed": True,
                "legacy_db_path": legacy_db.db_path,
            }
        )
    except Exception as exc:
        results.append({"test": "backwards_compatibility", "passed": False, "error": str(exc)})
        passed = False

    return {"passed": passed, "data_root": root, "results": results}


def main() -> int:
    try:
        summary = run_phase4a_tests()
    except Exception:
        traceback.print_exc()
        return 1

    print("Nexal Legal — Phase 4A Validation")
    print("=" * 40)
    for item in summary["results"]:
        status = "PASS" if item.get("passed") else "FAIL"
        name = item["test"]
        extra = ""
        if item.get("skipped"):
            extra = f" (skipped: {item.get('reason', '')})"
        elif not item.get("passed") and item.get("error"):
            extra = f" — {item['error']}"
        print(f"[{status}] {name}{extra}")

    print("=" * 40)
    print(f"Data root: {summary['data_root']}")
    if summary["passed"]:
        print("PHASE 4A VALIDATION: PASSED")
        return 0

    print("PHASE 4A VALIDATION: FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
