"""
Backup path and retention configuration for Nexal Legal Ledger.
"""
import os
from dataclasses import dataclass
from typing import Literal

from nexal_platform.config import get_platform_paths

ScheduleType = Literal["daily", "weekly", "monthly"]


@dataclass(frozen=True)
class BackupConfig:
    root: str
    daily_dir: str
    weekly_dir: str
    monthly_dir: str
    manifest_dir: str
    audit_log: str
    daily_retention_days: int
    weekly_retention_weeks: int
    monthly_retention_months: int
    compress: bool
    require_checksum: bool

    def schedule_dir(self, schedule: ScheduleType) -> str:
        if schedule == "daily":
            return self.daily_dir
        if schedule == "weekly":
            return self.weekly_dir
        return self.monthly_dir


def get_backup_config() -> BackupConfig:
    paths = get_platform_paths()
    backup_root = os.environ.get("NEXAL_BACKUP_DIR", "").strip() or os.path.join(
        paths.root, "backups"
    )
    backup_root = os.path.abspath(backup_root)

    return BackupConfig(
        root=backup_root,
        daily_dir=os.path.join(backup_root, "daily"),
        weekly_dir=os.path.join(backup_root, "weekly"),
        monthly_dir=os.path.join(backup_root, "monthly"),
        manifest_dir=os.path.join(backup_root, "manifests"),
        audit_log=os.path.join(backup_root, "audit.jsonl"),
        daily_retention_days=int(os.environ.get("BACKUP_DAILY_RETENTION_DAYS", "14")),
        weekly_retention_weeks=int(os.environ.get("BACKUP_WEEKLY_RETENTION_WEEKS", "8")),
        monthly_retention_months=int(os.environ.get("BACKUP_MONTHLY_RETENTION_MONTHS", "12")),
        compress=os.environ.get("BACKUP_COMPRESS", "1").strip() not in ("0", "false", "False"),
        require_checksum=os.environ.get("BACKUP_REQUIRE_CHECKSUM", "1").strip()
        not in ("0", "false", "False"),
    )
