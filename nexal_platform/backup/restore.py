"""
Restore tooling for platform and tenant databases with integrity checks.
"""
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from nexal_platform.backup.audit import append_audit
from nexal_platform.backup.config import BackupConfig, get_backup_config
from nexal_platform.backup.manifest import read_manifest, utc_now
from nexal_platform.backup.snapshot import (
    create_sqlite_snapshot,
    extract_database,
    verify_checksum,
    verify_zip,
)
from nexal_platform.config import get_platform_paths
from nexal_platform.platform_db import PlatformDatabase


class RestoreError(Exception):
    pass


class RestoreService:
    def __init__(
        self,
        config: Optional[BackupConfig] = None,
        platform_db: Optional[PlatformDatabase] = None,
    ):
        self.config = config or get_backup_config()
        self.paths = get_platform_paths()
        self.platform_db = platform_db or PlatformDatabase(self.paths)

    def _confirm(self, prompt: str, *, assume_yes: bool = False) -> None:
        if assume_yes:
            return
        if os.environ.get("NEXAL_RESTORE_ASSUME_YES", "").strip() in ("1", "true", "yes"):
            return
        answer = input(f"{prompt} [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            raise RestoreError("Restore cancelled by operator.")

    def _find_manifest_entry(
        self,
        manifest: Dict[str, Any],
        *,
        target_type: str,
        firm_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        for entry in manifest.get("entries", []):
            if entry.get("target_type") != target_type:
                continue
            if target_type == "tenant" and entry.get("firm_id") != firm_id:
                continue
            if not entry.get("success"):
                continue
            return entry
        raise RestoreError("No successful backup entry found in manifest.")

    def _validate_package(self, entry: Dict[str, Any]) -> str:
        backup_path = entry.get("backup_path") or ""
        if not backup_path or not os.path.isfile(backup_path):
            raise RestoreError(f"Backup file not found: {backup_path}")

        checksum = entry.get("checksum_sha256") or ""
        if self.config.require_checksum and not verify_checksum(backup_path, checksum):
            raise RestoreError(f"Checksum verification failed for {backup_path}")

        if backup_path.endswith(".zip") and not verify_zip(backup_path):
            raise RestoreError(f"ZIP integrity check failed for {backup_path}")

        return backup_path

    def restore_platform(
        self,
        manifest_path: str,
        *,
        assume_yes: bool = False,
    ) -> Dict[str, Any]:
        manifest = read_manifest(manifest_path)
        entry = self._find_manifest_entry(manifest, target_type="platform")
        backup_path = self._validate_package(entry)

        self._confirm(
            f"Restore platform.db from {backup_path}? This overwrites {self.paths.platform_db}.",
            assume_yes=assume_yes,
        )

        pre_restore = self.paths.platform_db + f".pre-restore-{utc_now().replace(':', '')}"
        if os.path.isfile(self.paths.platform_db):
            shutil.copy2(self.paths.platform_db, pre_restore)

        temp_db = backup_path + ".restore.db"
        try:
            extract_database(backup_path, temp_db)
            os.replace(temp_db, self.paths.platform_db)
        finally:
            if os.path.isfile(temp_db):
                os.remove(temp_db)

        append_audit(
            self.config,
            action="restore_platform",
            status="success",
            details={"manifest_path": manifest_path, "backup_path": backup_path},
        )
        return {
            "restored": "platform",
            "path": self.paths.platform_db,
            "pre_restore_copy": pre_restore if os.path.isfile(pre_restore) else None,
        }

    def restore_tenant(
        self,
        firm_id: str,
        manifest_path: str,
        *,
        assume_yes: bool = False,
    ) -> Dict[str, Any]:
        manifest = read_manifest(manifest_path)
        entry = self._find_manifest_entry(manifest, target_type="tenant", firm_id=firm_id)
        backup_path = self._validate_package(entry)

        if entry.get("firm_id") != firm_id:
            raise RestoreError("Manifest firm_id does not match restore target — aborting.")

        workspace = self.platform_db.get_workspace_for_firm(firm_id)
        target_path = workspace["database_path"]

        self._confirm(
            f"Restore tenant {firm_id} from {backup_path}? This overwrites {target_path}.",
            assume_yes=assume_yes,
        )

        pre_restore = target_path + f".pre-restore-{utc_now().replace(':', '')}"
        if os.path.isfile(target_path):
            shutil.copy2(target_path, pre_restore)

        temp_db = backup_path + ".restore.db"
        try:
            extract_database(backup_path, temp_db)
            os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
            os.replace(temp_db, target_path)
        finally:
            if os.path.isfile(temp_db):
                os.remove(temp_db)

        append_audit(
            self.config,
            action="restore_tenant",
            status="success",
            details={
                "firm_id": firm_id,
                "manifest_path": manifest_path,
                "backup_path": backup_path,
                "target_path": target_path,
            },
        )

        try:
            from db_router import clear_router_cache

            clear_router_cache()
        except Exception:
            pass

        return {
            "restored": "tenant",
            "firm_id": firm_id,
            "path": target_path,
            "pre_restore_copy": pre_restore if os.path.isfile(pre_restore) else None,
        }

    def restore_full(
        self,
        manifest_path: str,
        *,
        assume_yes: bool = False,
    ) -> Dict[str, Any]:
        manifest = read_manifest(manifest_path)
        self._confirm(
            f"Restore FULL system from manifest {manifest_path}? "
            "This overwrites platform.db and all tenant databases listed in the manifest.",
            assume_yes=assume_yes,
        )

        platform_result = self.restore_platform(manifest_path, assume_yes=True)
        tenant_results = []
        for entry in manifest.get("entries", []):
            if entry.get("target_type") != "tenant" or not entry.get("success"):
                continue
            firm_id = entry.get("firm_id")
            if not firm_id:
                continue
            tenant_results.append(self.restore_tenant(firm_id, manifest_path, assume_yes=True))

        append_audit(
            self.config,
            action="restore_full",
            status="success",
            details={"manifest_path": manifest_path, "tenant_count": len(tenant_results)},
        )
        return {"platform": platform_result, "tenants": tenant_results}
