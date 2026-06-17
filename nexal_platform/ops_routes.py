"""
Operational API routes for portal integration (backup health, monitoring).
"""
import os

from flask import jsonify, request

from nexal_platform.backup import BackupService


def _verify_ops_secret() -> bool:
    expected = (
        os.environ.get("NEXAL_OPS_SECRET", "").strip()
        or os.environ.get("BACKUP_HEALTH_SECRET", "").strip()
    )
    if not expected:
        return False
    provided = request.headers.get("X-Nexal-Ops-Secret", "").strip()
    return provided == expected


def register_ops_routes(app):
    @app.route("/api/ops/backup-health", methods=["GET"])
    def api_ops_backup_health():
        if not _verify_ops_secret():
            return jsonify({"error": "Unauthorized"}), 401

        service = BackupService()
        summary = service.health_summary()
        latest = summary.get("last_manifest") or {}
        return jsonify(
            {
                "system": "ledger",
                "restore_ready": summary.get("restore_ready", False),
                "backup_root": summary.get("backup_root"),
                "platform_db": summary.get("platform_db"),
                "tenant_count": summary.get("tenant_count"),
                "last_backup": {
                    "run_id": latest.get("run_id"),
                    "schedule": latest.get("schedule"),
                    "created_at": latest.get("created_at"),
                    "success": latest.get("success"),
                    "entry_count": latest.get("entry_count"),
                    "manifest_path": latest.get("_path"),
                },
                "recent_manifests": summary.get("recent_manifests", [])[:10],
                "recent_audit": summary.get("recent_audit", [])[:20],
            }
        ), 200
