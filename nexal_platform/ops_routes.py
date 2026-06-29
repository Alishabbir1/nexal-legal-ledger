"""
Operational API routes for portal integration (backup health, monitoring).
"""
import hmac
import logging
from typing import Tuple

from flask import jsonify, request

from nexal_platform.backup import BackupService
from nexal_platform.ops_secret import OPS_SECRET_HEADER, get_expected_ops_secret

logger = logging.getLogger(__name__)


def _verify_ops_secret() -> Tuple[bool, str]:
    expected = get_expected_ops_secret()
    provided = (request.headers.get(OPS_SECRET_HEADER) or "").strip().strip('"').strip("'")

    if not expected:
        return False, "server_secret_not_configured"
    if not provided:
        return False, "missing_ops_secret_header"
    if not hmac.compare_digest(provided, expected):
        return False, "invalid_ops_secret"
    return True, ""


def register_ops_routes(app):
    @app.route("/api/ops/backup-health", methods=["GET"])
    def api_ops_backup_health():
        ok, reason = _verify_ops_secret()
        if not ok:
            logger.warning("Ops backup health unauthorized: %s", reason)
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
