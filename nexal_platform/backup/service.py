"""
Multi-tenant backup orchestration for platform.db and all tenant databases.
"""
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from nexal_platform.backup.audit import append_audit
from nexal_platform.backup.config import BackupConfig, ScheduleType, get_backup_config
from nexal_platform.backup.manifest import build_manifest, utc_now, write_manifest
from nexal_platform.backup.snapshot import create_sqlite_snapshot, package_database, sha256_file
from nexal_platform.config import get_platform_paths
from nexal_platform.platform_db import PlatformDatabase


@dataclass
class BackupEntryResult:
    target_type: str
    firm_id: Optional[str]
    firm_name: Optional[str]
    source_path: str
    backup_path: str
    checksum_sha256: str
    size_bytes: int
    success: bool
    error: Optional[str] = None


@dataclass
class BackupRunResult:
    run_id: str
    schedule: ScheduleType
    success: bool
    manifest_path: Optional[str]
    entries: List[BackupEntryResult] = field(default_factory=list)
    error: Optional[str] = None


class BackupService:
    def __init__(
        self,
        config: Optional[BackupConfig] = None,
        platform_db: Optional[PlatformDatabase] = None,
    ):
        self.config = config or get_backup_config()
        self.paths = get_platform_paths()
        self.platform_db = platform_db or PlatformDatabase(self.paths)

    def _ensure_schedule_dirs(self, schedule: ScheduleType) -> str:
        target = self.config.schedule_dir(schedule)
        os.makedirs(target, exist_ok=True)
        return target

    def _backup_single_db(
        self,
        *,
        schedule: ScheduleType,
        schedule_dir: str,
        run_id: str,
        source_path: str,
        target_type: str,
        firm_id: Optional[str] = None,
        firm_name: Optional[str] = None,
        portal_firm_id: Optional[str] = None,
    ) -> BackupEntryResult:
        timestamp = utc_now().replace(":", "").replace("-", "")
        suffix = ".zip" if self.config.compress else ".db"
        label = firm_id or "platform"
        backup_name = f"{target_type}_{label}_{timestamp}_{run_id[:8]}{suffix}"
        backup_path = os.path.join(schedule_dir, backup_name)
        snapshot_path = backup_path + ".snapshot.db"

        try:
            create_sqlite_snapshot(source_path, snapshot_path)
            if self.config.compress:
                checksum, size = package_database(snapshot_path, backup_path, compress=True)
            else:
                os.replace(snapshot_path, backup_path)
                checksum = sha256_file(backup_path)
                size = os.path.getsize(backup_path)
                snapshot_path = ""

            if snapshot_path and os.path.isfile(snapshot_path):
                os.remove(snapshot_path)

            if self.config.require_checksum and not checksum:
                raise RuntimeError("Checksum generation failed.")

            return BackupEntryResult(
                target_type=target_type,
                firm_id=firm_id,
                firm_name=firm_name,
                source_path=source_path,
                backup_path=backup_path,
                checksum_sha256=checksum,
                size_bytes=size,
                success=True,
            )
        except Exception as exc:
            for path in (snapshot_path, backup_path):
                if path and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            return BackupEntryResult(
                target_type=target_type,
                firm_id=firm_id,
                firm_name=firm_name,
                source_path=source_path,
                backup_path=backup_path,
                checksum_sha256="",
                size_bytes=0,
                success=False,
                error=str(exc),
            )

    def backup_platform(self, schedule: ScheduleType, schedule_dir: str, run_id: str) -> BackupEntryResult:
        return self._backup_single_db(
            schedule=schedule,
            schedule_dir=schedule_dir,
            run_id=run_id,
            source_path=self.paths.platform_db,
            target_type="platform",
        )

    def backup_tenants(self, schedule: ScheduleType, schedule_dir: str, run_id: str) -> List[BackupEntryResult]:
        results: List[BackupEntryResult] = []
        for firm in self.platform_db.list_firms():
            if firm.get("status") != "active":
                continue
            firm_id = firm["id"]
            try:
                workspace = self.platform_db.get_workspace_for_firm(firm_id)
            except KeyError:
                results.append(
                    BackupEntryResult(
                        target_type="tenant",
                        firm_id=firm_id,
                        firm_name=firm.get("name"),
                        source_path="",
                        backup_path="",
                        checksum_sha256="",
                        size_bytes=0,
                        success=False,
                        error="Workspace not found.",
                    )
                )
                continue

            db_path = workspace["database_path"]
            if not os.path.isfile(db_path):
                results.append(
                    BackupEntryResult(
                        target_type="tenant",
                        firm_id=firm_id,
                        firm_name=firm.get("name"),
                        source_path=db_path,
                        backup_path="",
                        checksum_sha256="",
                        size_bytes=0,
                        success=False,
                        error="Tenant database file missing.",
                    )
                )
                continue

            results.append(
                self._backup_single_db(
                    schedule=schedule,
                    schedule_dir=schedule_dir,
                    run_id=run_id,
                    source_path=db_path,
                    target_type="tenant",
                    firm_id=firm_id,
                    firm_name=firm.get("name"),
                    portal_firm_id=firm.get("portal_firm_id"),
                )
            )
        return results

    def run_backup(self, schedule: ScheduleType = "daily") -> BackupRunResult:
        run_id = str(uuid.uuid4())
        schedule_dir = self._ensure_schedule_dirs(schedule)
        os.makedirs(self.config.manifest_dir, exist_ok=True)

        append_audit(
            self.config,
            action="backup_start",
            status="started",
            details={"run_id": run_id, "schedule": schedule},
        )

        entries: List[BackupEntryResult] = []
        entries.append(self.backup_platform(schedule, schedule_dir, run_id))
        entries.extend(self.backup_tenants(schedule, schedule_dir, run_id))

        success = all(entry.success for entry in entries) and len(entries) > 0
        manifest_entries: List[Dict[str, Any]] = []
        for entry in entries:
            manifest_entries.append(
                {
                    "target_type": entry.target_type,
                    "firm_id": entry.firm_id,
                    "firm_name": entry.firm_name,
                    "source_path": entry.source_path,
                    "backup_path": entry.backup_path,
                    "checksum_sha256": entry.checksum_sha256,
                    "size_bytes": entry.size_bytes,
                    "success": entry.success,
                    "error": entry.error,
                }
            )

        manifest = build_manifest(
            schedule,
            run_id,
            manifest_entries,
            success=success,
            error=None if success else "One or more backup targets failed.",
        )
        manifest_path = write_manifest(self.config, schedule, manifest)

        self.apply_retention(schedule)
        append_audit(
            self.config,
            action="backup_complete",
            status="success" if success else "failed",
            details={
                "run_id": run_id,
                "schedule": schedule,
                "manifest_path": manifest_path,
                "entry_count": len(entries),
                "failed": [e.firm_id or "platform" for e in entries if not e.success],
            },
        )

        return BackupRunResult(
            run_id=run_id,
            schedule=schedule,
            success=success,
            manifest_path=manifest_path,
            entries=entries,
            error=None if success else "Backup completed with failures.",
        )

    def apply_retention(self, schedule: ScheduleType) -> None:
        """Remove backups older than configured retention for the schedule tier."""
        schedule_dir = self.config.schedule_dir(schedule)
        if not os.path.isdir(schedule_dir):
            return

        now = datetime.now(timezone.utc)
        if schedule == "daily":
            cutoff = now - timedelta(days=self.config.daily_retention_days)
        elif schedule == "weekly":
            cutoff = now - timedelta(weeks=self.config.weekly_retention_weeks)
        else:
            cutoff = now - timedelta(days=self.config.monthly_retention_months * 30)

        for name in os.listdir(schedule_dir):
            path = os.path.join(schedule_dir, name)
            if not os.path.isfile(path):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
            if mtime < cutoff:
                try:
                    os.remove(path)
                    append_audit(
                        self.config,
                        action="retention_prune",
                        status="success",
                        details={"path": path, "schedule": schedule},
                    )
                except OSError as exc:
                    append_audit(
                        self.config,
                        action="retention_prune",
                        status="failed",
                        details={"path": path, "error": str(exc)},
                    )

    def health_summary(self) -> Dict[str, Any]:
        from nexal_platform.backup.manifest import latest_manifest, list_manifests
        from nexal_platform.backup.audit import read_recent_audit

        latest = latest_manifest(self.config)
        return {
            "backup_root": self.config.root,
            "platform_db": self.paths.platform_db,
            "tenant_count": len(
                [f for f in self.platform_db.list_firms() if f.get("status") == "active"]
            ),
            "last_manifest": latest,
            "recent_manifests": list_manifests(self.config, limit=10),
            "recent_audit": read_recent_audit(self.config, limit=20),
            "restore_ready": bool(latest and latest.get("success")),
        }
