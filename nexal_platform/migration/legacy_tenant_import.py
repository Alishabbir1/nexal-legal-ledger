"""
One-time legacy desktop ledger import into an existing multi-tenant workspace.

Copies a legacy solicitor_ledger.db into the tenant path registered for a
Portal firm, applies non-destructive schema migrations, and validates that
accounting totals are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from database import Database
from lib.firm_package import cache_tier_in_tenant_db
from nexal_platform.config import (
    get_platform_paths,
    repair_all_stale_workspace_paths,
    require_safe_tenant_db_path,
    resolve_workspace_database_path,
)
from nexal_platform.platform_db import PlatformDatabase


ACCOUNTING_TABLES = (
    "clients",
    "ledger_transactions",
    "cashbook_transactions",
    "office_cashbook",
    "office_fee_transfers",
    "reconciliations",
    "reconciliation_bank_session",
    "cheque_status_log",
    "month_locks",
    "audit_trail",
    "audit_log",
    "system_config",
    "users",
)

EXPECTED_APRIL_CASHBOOK = Decimal("36214.51")


@dataclass
class TenantSnapshot:
    db_path: str
    table_counts: Dict[str, int]
    cashbook_balance: Decimal
    office_balance: Decimal
    total_ledger_balance: Decimal
    client_balances: Dict[int, Decimal]
    april_reconciliation: Optional[Dict[str, Any]]
    checksums: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["cashbook_balance"] = str(self.cashbook_balance)
        data["office_balance"] = str(self.office_balance)
        data["total_ledger_balance"] = str(self.total_ledger_balance)
        data["client_balances"] = {str(k): str(v) for k, v in self.client_balances.items()}
        return data


@dataclass
class MigrationResult:
    status: str
    portal_firm_id: str
    platform_firm_id: str
    tenant_database_path: str
    legacy_path: str
    backup_path: Optional[str]
    before_tenant_snapshot: Optional[TenantSnapshot]
    legacy_snapshot: TenantSnapshot
    after_snapshot: TenantSnapshot
    validation_passed: bool
    validation_errors: List[str]


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _raw_cashbook_balance(conn: sqlite3.Connection) -> Decimal:
    if not _table_exists(conn, "cashbook_transactions"):
        return Decimal("0")
    total = Decimal("0")
    rows = conn.execute(
        "SELECT transaction_type, amount FROM cashbook_transactions ORDER BY id"
    ).fetchall()
    for row in rows:
        tt = (row["transaction_type"] or "").strip().lower()
        amt = Decimal(str(row["amount"]))
        if tt in ("receipt", "transfer in", "transfer_in"):
            total += amt
        else:
            total -= amt
    return total


def _raw_office_balance(conn: sqlite3.Connection) -> Decimal:
    if not _table_exists(conn, "office_cashbook"):
        return Decimal("0")
    total = Decimal("0")
    rows = conn.execute(
        "SELECT transaction_type, amount FROM office_cashbook ORDER BY id"
    ).fetchall()
    for row in rows:
        tt = (row["transaction_type"] or "").strip().lower()
        amt = Decimal(str(row["amount"]))
        if tt in ("income", "receipt", "transfer in", "transfer_in"):
            total += amt
        else:
            total -= amt
    return total


def _table_checksum(conn: sqlite3.Connection, table: str) -> str:
    if not _table_exists(conn, table):
        return ""
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    payload = json.dumps([dict(r) for r in rows], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot_tenant(db_path: str) -> TenantSnapshot:
    """Capture accounting counts and balances from a tenant database file."""
    db_path = os.path.abspath(db_path)
    conn = _connect(db_path)
    try:
        table_counts: Dict[str, int] = {}
        checksums: Dict[str, str] = {}
        for table in ACCOUNTING_TABLES:
            if _table_exists(conn, table):
                table_counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                checksums[table] = _table_checksum(conn, table)

        april: Optional[Dict[str, Any]] = None
        if _table_exists(conn, "reconciliations"):
            cols = {r[1] for r in conn.execute("PRAGMA table_info(reconciliations)").fetchall()}
            if "is_current" in cols:
                row = conn.execute(
                    """
                    SELECT * FROM reconciliations
                    WHERE reconciliation_month = 4 AND reconciliation_year = 2026
                      AND COALESCE(is_deleted, 0) = 0
                      AND COALESCE(is_current, 1) = 1
                    ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM reconciliations
                    WHERE reconciliation_month = 4 AND reconciliation_year = 2026
                      AND COALESCE(is_deleted, 0) = 0
                    ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
            if row:
                april = dict(row)

        db = Database(db_path=db_path, skip_user_seed=True)
        client_balances: Dict[int, Decimal] = {}
        if _table_exists(conn, "clients"):
            for client_row in conn.execute("SELECT id FROM clients ORDER BY id").fetchall():
                cid = int(client_row["id"])
                client_balances[cid] = db.get_client_balance(cid)

        return TenantSnapshot(
            db_path=db_path,
            table_counts=table_counts,
            cashbook_balance=_raw_cashbook_balance(conn),
            office_balance=_raw_office_balance(conn),
            total_ledger_balance=db.get_total_ledger_balance(),
            client_balances=client_balances,
            april_reconciliation=april,
            checksums=checksums,
        )
    finally:
        conn.close()


def compare_snapshots(
    legacy: TenantSnapshot,
    migrated: TenantSnapshot,
) -> List[str]:
    """Return validation errors; empty list means accounting parity."""
    errors: List[str] = []

    for table, legacy_count in legacy.table_counts.items():
        migrated_count = migrated.table_counts.get(table)
        if migrated_count != legacy_count:
            if table in ("system_config", "users") and migrated_count is not None and migrated_count >= legacy_count:
                continue
            errors.append(f"Table {table}: legacy count {legacy_count} != migrated {migrated_count}")

    if legacy.cashbook_balance != migrated.cashbook_balance:
        errors.append(
            f"Cashbook balance mismatch: legacy {legacy.cashbook_balance} != migrated {migrated.cashbook_balance}"
        )

    if legacy.office_balance != migrated.office_balance:
        errors.append(
            f"Office balance mismatch: legacy {legacy.office_balance} != migrated {migrated.office_balance}"
        )

    if legacy.total_ledger_balance != migrated.total_ledger_balance:
        errors.append(
            f"Total ledger balance mismatch: legacy {legacy.total_ledger_balance} != migrated {migrated.total_ledger_balance}"
        )

    for client_id, legacy_bal in legacy.client_balances.items():
        migrated_bal = migrated.client_balances.get(client_id)
        if migrated_bal != legacy_bal:
            errors.append(
                f"Client {client_id} balance mismatch: legacy {legacy_bal} != migrated {migrated_bal}"
            )

    legacy_april_total = None
    if legacy.april_reconciliation:
        legacy_april_total = Decimal(str(legacy.april_reconciliation.get("cashbook_total", "0")))
    migrated_april_total = None
    if migrated.april_reconciliation:
        migrated_april_total = Decimal(str(migrated.april_reconciliation.get("cashbook_total", "0")))

    if legacy_april_total != migrated_april_total:
        errors.append(
            f"April reconciliation cashbook_total mismatch: legacy {legacy_april_total} != migrated {migrated_april_total}"
        )

    for table in (
        "clients",
        "ledger_transactions",
        "cashbook_transactions",
        "office_cashbook",
        "office_fee_transfers",
        "cheque_status_log",
        "month_locks",
        "audit_trail",
        "audit_log",
    ):
        if table not in legacy.checksums:
            continue
        if legacy.checksums.get(table) != migrated.checksums.get(table):
            errors.append(f"Checksum mismatch for table {table}")

    return errors


def _firm_has_user_email(platform: PlatformDatabase, firm_id: str, email: str) -> bool:
    conn = platform.get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE firm_id = ? AND lower(email) = lower(?) LIMIT 1",
            (firm_id, email),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _resolve_existing_portal_firm(platform: PlatformDatabase, portal_firm_id: str) -> Dict[str, Any]:
    firm = platform.get_firm_by_portal_firm_id(portal_firm_id)
    if firm is None:
        raise ValueError(
            f"No existing ledger tenant linked to portal firm {portal_firm_id}. "
            "Provision the firm first; this migration never creates a new tenant."
        )
    return firm


def _backup_tenant_db(tenant_path: str, backup_dir: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, f"pre_migration_{_utc_stamp()}.db")
    if os.path.isfile(tenant_path):
        shutil.copy2(tenant_path, backup_path)
    return backup_path


def _apply_tenant_metadata(
    tenant_db: Database,
    *,
    platform_firm_id: str,
    portal_firm_id: str,
    subscription_tier: str,
) -> None:
    """Multi-tenant metadata only — never touches accounting rows."""
    tenant_db.initialize_security_columns()
    cache_tier_in_tenant_db(tenant_db, subscription_tier)
    tenant_db.set_config(
        "provisioned_tenant",
        "1",
        "Multi-tenant firm database — legacy production import",
    )
    tenant_db.set_config(
        "portal_firm_id",
        portal_firm_id,
        "Portal firms.id for SSO routing audit",
    )
    conn = tenant_db.get_connection()
    try:
        conn.execute(
            "UPDATE users SET firm_id = ? WHERE COALESCE(is_system, 0) = 0 OR username NOT IN ('admin', 'staff')",
            (platform_firm_id,),
        )
        conn.commit()
    finally:
        conn.close()


def migrate_legacy_into_existing_tenant(
    legacy_path: str,
    portal_firm_id: str,
    *,
    owner_email: Optional[str] = None,
    portal_user_id: Optional[str] = None,
    subscription_tier: str = "essential",
    dry_run: bool = False,
    backup_root: Optional[str] = None,
) -> MigrationResult:
    """
    Import legacy desktop DB into the existing tenant for portal_firm_id.

    Never creates a new platform firm or workspace.
    """
    legacy_path = os.path.abspath(legacy_path)
    if not os.path.isfile(legacy_path):
        raise FileNotFoundError(f"Legacy database not found: {legacy_path}")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        legacy_copy = tmp.name
    shutil.copy2(legacy_path, legacy_copy)
    try:
        legacy_snapshot = snapshot_tenant(legacy_copy)
    finally:
        os.remove(legacy_copy)
    if legacy_snapshot.table_counts.get("clients", 0) == 0:
        raise ValueError("Legacy database has no clients — refusing to migrate an empty source.")

    paths = get_platform_paths()
    platform = PlatformDatabase(paths)
    firm = _resolve_existing_portal_firm(platform, portal_firm_id)
    platform_firm_id = firm["id"]
    repair_all_stale_workspace_paths(platform)
    workspace = platform.get_workspace_for_firm(platform_firm_id)
    tenant_path = paths.tenant_db_path(platform_firm_id)
    resolve_workspace_database_path(
        platform,
        platform_firm_id,
        workspace["database_path"],
        paths,
    )
    platform.update_workspace_database_path(platform_firm_id, tenant_path)
    tenant_path = require_safe_tenant_db_path(
        tenant_path,
        context="migrate_legacy_into_existing_tenant",
    )

    before_snapshot: Optional[TenantSnapshot] = None
    if os.path.isfile(tenant_path):
        before_snapshot = snapshot_tenant(tenant_path)

    backup_path: Optional[str] = None
    if not dry_run:
        backup_root = backup_root or os.path.join(paths.root, "backups", "migration")
        backup_path = _backup_tenant_db(tenant_path, backup_root)
        os.makedirs(os.path.dirname(tenant_path), exist_ok=True)
        staging_path = tenant_path + ".migrating"
        shutil.copy2(legacy_path, staging_path)
        os.replace(staging_path, tenant_path)
        tenant_db = Database(db_path=tenant_path, skip_user_seed=True)
        tier = firm.get("subscription_tier") or subscription_tier
        _apply_tenant_metadata(
            tenant_db,
            platform_firm_id=platform_firm_id,
            portal_firm_id=portal_firm_id,
            subscription_tier=tier,
        )
        if owner_email and not _firm_has_user_email(platform, platform_firm_id, owner_email):
            platform.create_user(
                firm_id=platform_firm_id,
                email=owner_email,
                portal_user_id=portal_user_id,
            )
        from nexal_platform.migration.tenant_permissions import repair_runtime_data_ownership

        repair_runtime_data_ownership(paths)
    else:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            staging_path = tmp.name
        shutil.copy2(legacy_path, staging_path)
        tenant_db = Database(db_path=staging_path, skip_user_seed=True)
        _apply_tenant_metadata(
            tenant_db,
            platform_firm_id=platform_firm_id,
            portal_firm_id=portal_firm_id,
            subscription_tier=firm.get("subscription_tier") or subscription_tier,
        )
        tenant_path = staging_path

    after_snapshot = snapshot_tenant(tenant_path)
    validation_errors = compare_snapshots(legacy_snapshot, after_snapshot)

    if dry_run and os.path.isfile(tenant_path):
        os.remove(tenant_path)

    return MigrationResult(
        status="dry_run" if dry_run else "migrated",
        portal_firm_id=portal_firm_id,
        platform_firm_id=platform_firm_id,
        tenant_database_path=tenant_path,
        legacy_path=legacy_path,
        backup_path=backup_path,
        before_tenant_snapshot=before_snapshot,
        legacy_snapshot=legacy_snapshot,
        after_snapshot=after_snapshot,
        validation_passed=len(validation_errors) == 0,
        validation_errors=validation_errors,
    )
