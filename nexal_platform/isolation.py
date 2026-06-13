"""
Tenant isolation verification helpers for Phase 4A acceptance checks.
"""
import os
import sqlite3
import tempfile
import uuid

from database import Database

from nexal_platform.provision import provision_firm
from nexal_platform.router import TenantRouter


def verify_tenant_isolation(paths_root: str | None = None) -> dict:
    """
    Provision two firms, write distinct data to each, and verify no cross-read.

    Returns a result dict suitable for automated acceptance checks.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = paths_root or os.path.join(tmp, "nexal-test-data")
        os.environ["NEXAL_DATA_DIR"] = root

        firm_a = provision_firm(name="Firm Alpha", slug=f"alpha-{uuid.uuid4().hex[:8]}", owner_email="alpha@example.com")
        firm_b = provision_firm(name="Firm Beta", slug=f"beta-{uuid.uuid4().hex[:8]}", owner_email="beta@example.com")

        router = TenantRouter()
        db_a = router.get_database(firm_a["firm"]["id"])
        db_b = router.get_database(firm_b["firm"]["id"])

        _insert_marker_client(db_a, "ALPHA-ONLY")
        _insert_marker_client(db_b, "BETA-ONLY")

        alpha_clients = _list_client_codes(db_a)
        beta_clients = _list_client_codes(db_b)

        passed = (
            "ALPHA-ONLY" in alpha_clients
            and "BETA-ONLY" in beta_clients
            and "BETA-ONLY" not in alpha_clients
            and "ALPHA-ONLY" not in beta_clients
            and firm_a["database_path"] != firm_b["database_path"]
            and os.path.isfile(firm_a["database_path"])
            and os.path.isfile(firm_b["database_path"])
        )

        return {
            "passed": passed,
            "firm_a_path": firm_a["database_path"],
            "firm_b_path": firm_b["database_path"],
            "firm_a_clients": alpha_clients,
            "firm_b_clients": beta_clients,
        }


def _insert_marker_client(db: Database, client_code: str) -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO clients (client_code, client_name, matter_reference, description)
            VALUES (?, ?, ?, ?)
            """,
            (client_code, f"Client {client_code}", "MAT-001", "Isolation test marker"),
        )
        conn.commit()
    finally:
        conn.close()


def _list_client_codes(db: Database) -> list[str]:
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT client_code FROM clients ORDER BY client_code").fetchall()
        return [row["client_code"] for row in rows]
    finally:
        conn.close()
