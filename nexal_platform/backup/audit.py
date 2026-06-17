"""
Backup audit logging (JSONL under NEXAL_DATA_DIR/backups).
"""
import json
import os
from typing import Any, Dict, Optional

from nexal_platform.backup.config import BackupConfig
from nexal_platform.backup.manifest import utc_now


def append_audit(
    config: BackupConfig,
    *,
    action: str,
    status: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    os.makedirs(os.path.dirname(config.audit_log) or ".", exist_ok=True)
    record = {
        "timestamp": utc_now(),
        "action": action,
        "status": status,
        "details": details or {},
    }
    with open(config.audit_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def read_recent_audit(config: BackupConfig, limit: int = 100) -> list[dict]:
    if not os.path.isfile(config.audit_log):
        return []

    lines: list[str] = []
    with open(config.audit_log, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                lines.append(line)

    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(records))
