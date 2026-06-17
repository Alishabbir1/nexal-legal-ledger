"""
Backup manifest generation and persistence.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nexal_platform.backup.config import BackupConfig, ScheduleType


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_manifest(
    schedule: ScheduleType,
    run_id: str,
    entries: List[Dict[str, Any]],
    *,
    success: bool,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "version": 1,
        "run_id": run_id,
        "schedule": schedule,
        "created_at": utc_now(),
        "success": success,
        "error": error,
        "entry_count": len(entries),
        "entries": entries,
    }


def write_manifest(config: BackupConfig, schedule: ScheduleType, manifest: Dict[str, Any]) -> str:
    os.makedirs(config.manifest_dir, exist_ok=True)
    filename = f"{schedule}_{manifest['run_id']}.json"
    path = os.path.join(config.manifest_dir, filename)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    return path


def read_manifest(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def latest_manifest(config: BackupConfig, schedule: Optional[ScheduleType] = None) -> Optional[Dict[str, Any]]:
    if not os.path.isdir(config.manifest_dir):
        return None

    prefix = f"{schedule}_" if schedule else ""
    candidates = sorted(
        [
            os.path.join(config.manifest_dir, name)
            for name in os.listdir(config.manifest_dir)
            if name.startswith(prefix) and name.endswith(".json")
        ],
        reverse=True,
    )
    if not candidates:
        return None
    return read_manifest(candidates[0])


def list_manifests(config: BackupConfig, limit: int = 50) -> List[Dict[str, Any]]:
    if not os.path.isdir(config.manifest_dir):
        return []

    paths = sorted(
        [
            os.path.join(config.manifest_dir, name)
            for name in os.listdir(config.manifest_dir)
            if name.endswith(".json")
        ],
        reverse=True,
    )[:limit]

    results: List[Dict[str, Any]] = []
    for path in paths:
        try:
            data = read_manifest(path)
            data["_path"] = path
            results.append(data)
        except Exception:
            continue
    return results
