"""Phase 5.4 backup and disaster recovery tests."""
import json
import os
import tempfile
import uuid

import pytest

from nexal_platform.backup import BackupService, RestoreService
from nexal_platform.backup.manifest import build_manifest, read_manifest, write_manifest
from nexal_platform.backup.snapshot import (
    create_sqlite_snapshot,
    package_database,
    sha256_file,
    verify_checksum,
    verify_zip,
)
from nexal_platform.backup.config import get_backup_config
from nexal_platform.provision import provision_firm


@pytest.fixture()
def isolated_data_root(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "nexal-data")
        monkeypatch.setenv("NEXAL_DATA_DIR", root)
        yield root


def _provision_sample_firm(isolated_data_root):
    slug = f"backup-firm-{uuid.uuid4().hex[:8]}"
    return provision_firm(name="Backup Test LLP", slug=slug, firm_code=f"BK{uuid.uuid4().hex[:4].upper()}")


def test_backup_manifest_generation(isolated_data_root):
    manifest = build_manifest(
        "daily",
        "run-123",
        [{"target_type": "platform", "success": True}],
        success=True,
    )
    assert manifest["version"] == 1
    assert manifest["schedule"] == "daily"
    assert manifest["success"] is True
    assert manifest["entry_count"] == 1


def test_backup_creation_platform_and_tenant(isolated_data_root):
    provisioned = _provision_sample_firm(isolated_data_root)
    service = BackupService()
    result = service.run_backup(schedule="daily")

    assert result.success is True
    assert result.manifest_path and os.path.isfile(result.manifest_path)
    assert len(result.entries) >= 2

    platform_entries = [e for e in result.entries if e.target_type == "platform"]
    tenant_entries = [e for e in result.entries if e.target_type == "tenant"]
    assert len(platform_entries) == 1
    assert platform_entries[0].success
    assert platform_entries[0].checksum_sha256
    assert len(tenant_entries) >= 1
    assert tenant_entries[0].firm_id == provisioned["firm"]["id"]
    assert os.path.isfile(tenant_entries[0].backup_path)


def test_checksum_validation(isolated_data_root):
    provisioned = _provision_sample_firm(isolated_data_root)
    db_path = provisioned["database_path"]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = os.path.join(tmp, "snap.db")
        package = os.path.join(tmp, "backup.zip")
        create_sqlite_snapshot(db_path, snapshot)
        checksum, _size = package_database(snapshot, package, compress=True)
        assert verify_checksum(package, checksum)
        assert verify_zip(package)
        assert checksum == sha256_file(package)


def test_backup_retention_prunes_old_files(isolated_data_root):
    _provision_sample_firm(isolated_data_root)
    service = BackupService()
    config = get_backup_config()
    schedule_dir = config.schedule_dir("daily")
    os.makedirs(schedule_dir, exist_ok=True)

    old_file = os.path.join(schedule_dir, "stale_backup.zip")
    with open(old_file, "wb") as handle:
        handle.write(b"stale")

    old_ts = 0
    os.utime(old_file, (old_ts, old_ts))

    service.apply_retention("daily")
    assert not os.path.isfile(old_file)


def test_tenant_restore(isolated_data_root):
    provisioned = _provision_sample_firm(isolated_data_root)
    firm_id = provisioned["firm"]["id"]
    db_path = provisioned["database_path"]

    backup_service = BackupService()
    backup_result = backup_service.run_backup(schedule="daily")
    assert backup_result.success

    with open(db_path, "wb") as handle:
        handle.write(b"corrupted")

    restore_service = RestoreService()
    restored = restore_service.restore_tenant(
        firm_id,
        backup_result.manifest_path,
        assume_yes=True,
    )
    assert restored["firm_id"] == firm_id
    assert os.path.isfile(db_path)
    assert os.path.getsize(db_path) > 100


def test_platform_restore(isolated_data_root):
    _provision_sample_firm(isolated_data_root)
    from nexal_platform.config import get_platform_paths

    paths = get_platform_paths()
    backup_service = BackupService()
    backup_result = backup_service.run_backup(schedule="daily")
    assert backup_result.success

    restore_service = RestoreService()

    with open(paths.platform_db, "wb") as handle:
        handle.write(b"corrupted")

    restored = restore_service.restore_platform(backup_result.manifest_path, assume_yes=True)
    assert restored["restored"] == "platform"
    assert os.path.isfile(paths.platform_db)
    assert os.path.getsize(paths.platform_db) > 100


def test_manifest_persist_and_read(isolated_data_root):
    config = get_backup_config()
    manifest = build_manifest("weekly", "abc", [], success=True)
    path = write_manifest(config, "weekly", manifest)
    loaded = read_manifest(path)
    assert loaded["run_id"] == "abc"
    assert loaded["schedule"] == "weekly"


def test_health_summary_reports_restore_ready(isolated_data_root):
    _provision_sample_firm(isolated_data_root)
    service = BackupService()
    service.run_backup(schedule="daily")
    summary = service.health_summary()
    assert summary["restore_ready"] is True
    assert summary["tenant_count"] >= 1
    assert summary["last_manifest"] is not None


def test_backup_health_api_bypasses_login_and_requires_ops_secret(isolated_data_root, monkeypatch):
    import app as ledger_app

    monkeypatch.setenv("NEXAL_OPS_SECRET", "phase54-ops-test-secret")
    client = ledger_app.app.test_client()

    response = client.get("/api/ops/backup-health")
    assert response.status_code == 401
    assert response.is_json
    assert response.get_json()["error"] == "Unauthorized"
    assert "reason" not in response.get_json()

    response = client.get(
        "/api/ops/backup-health",
        headers={"X-Nexal-Ops-Secret": "wrong-secret"},
    )
    assert response.status_code == 401

    response = client.get(
        "/api/ops/backup-health",
        headers={"X-Nexal-Ops-Secret": "phase54-ops-test-secret"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["system"] == "ledger"
    assert "restore_ready" in payload
