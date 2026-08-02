#!/usr/bin/env bash
# Restore production Ledger deploy — same process as office bank statement import.
# Run on VPS as root (IONOS console or SSH):
#   curl -fsSL https://raw.githubusercontent.com/Alishabbir1/nexal-legal-ledger/main/scripts/deploy_ledger_production.sh | bash
#
# Canonical production paths (from PHASE4B / configure_production_env):
#   APP_DIR:  /opt/nexal-legal-ledger  (preferred) or /opt/nexal-ledger
#   DATA_DIR: /var/lib/nexal-legal
#   SERVICE:  nexal-ledger
#   USER:     sync (gunicorn — cannot read /root/)
set -euo pipefail

SERVICE="${SERVICE:-nexal-ledger}"
DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"
GITHUB_REPO="${NEXAL_GITHUB_REPO:-https://github.com/Alishabbir1/nexal-legal-ledger.git}"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
DROPIN_FILE="${DROPIN_DIR}/working-directory.conf"
EXPECTED_MARKER="VAT-COLUMN-ENABLED-v2"

echo "=== Nexal Ledger — production deploy (bank-import process) ==="

resolve_service_user() {
  local u
  u="$(systemctl show "${SERVICE}" -p User --value 2>/dev/null || true)"
  if [[ -n "${u}" && "${u}" != "root" && "${u}" != "0" ]]; then
    echo "${u}"
    return 0
  fi
  echo "sync"
}

find_or_create_app_dir() {
  local candidate
  for candidate in \
    "${APP_DIR:-}" \
    "/opt/nexal-legal-ledger" \
    "/opt/nexal-ledger"; do
    [[ -n "${candidate}" ]] || continue
    if [[ -d "${candidate}/.git" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  local target="/opt/nexal-legal-ledger"
  echo "Cloning repository to ${target}..."
  git clone "${GITHUB_REPO}" "${target}"
  echo "${target}"
}

SERVICE_USER="$(resolve_service_user)"
APP_DIR="$(find_or_create_app_dir)"

echo "Service user: ${SERVICE_USER}"
echo "APP_DIR: ${APP_DIR}"
echo "DATA_DIR: ${DATA_DIR}"

CURRENT_WD="$(systemctl show "${SERVICE}" -p WorkingDirectory --value 2>/dev/null || true)"
echo "Current WorkingDirectory: ${CURRENT_WD:-unknown}"

if [[ "${CURRENT_WD}" == "/root/nexal-legal-ledger" ]] || [[ "${CURRENT_WD}" == "/opt/nexal-legal" ]]; then
  echo "WARNING: WorkingDirectory was wrong (${CURRENT_WD}). Fixing to ${APP_DIR}."
fi

echo ""
echo "[1/7] Install systemd WorkingDirectory drop-in..."
mkdir -p "${DROPIN_DIR}"
cat > "${DROPIN_FILE}" <<EOF
[Service]
WorkingDirectory=${APP_DIR}
EOF
echo "Wrote ${DROPIN_FILE}:"
cat "${DROPIN_FILE}"

echo ""
echo "[2/7] Ensure ${SERVICE_USER} can read application code..."
chown -R root:"${SERVICE_USER}" "${APP_DIR}" 2>/dev/null || chown -R root:root "${APP_DIR}"
chmod -R u=rwX,g=rX,o=rX "${APP_DIR}"

echo ""
echo "[3/7] Pull latest main into ${APP_DIR}..."
cd "${APP_DIR}"
git fetch origin main
git checkout main
git pull origin main
echo "HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"

echo ""
echo "[4/7] Clear stale bytecode..."
find "${APP_DIR}" -name "*.pyc" -delete 2>/dev/null || true
find "${APP_DIR}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo ""
echo "[5/7] Verify VAT template on disk..."
if ! grep -q "${EXPECTED_MARKER}" "${APP_DIR}/templates/office_import_review.html"; then
  echo "ERROR: ${EXPECTED_MARKER} missing — wrong code version." >&2
  exit 1
fi
grep -n "${EXPECTED_MARKER}" "${APP_DIR}/templates/office_import_review.html" | head -2

echo ""
echo "[6/7] Migrate tenant databases + repair data ownership..."
export NEXAL_DATA_DIR="${DATA_DIR}"
export PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export SERVICE="${SERVICE}"
export NEXAL_SERVICE_USER="${SERVICE_USER}"

python3 <<'PY'
import glob
import json
import os
from database import Database
from nexal_platform.migration.tenant_permissions import repair_runtime_data_ownership

data_root = os.environ["NEXAL_DATA_DIR"]
paths = glob.glob(f"{data_root}/tenants/*/solicitor_ledger.db")
legacy = f"{data_root}/solicitor_ledger.db"
if os.path.isfile(legacy):
    paths.insert(0, legacy)
for path in paths:
    print("Migrating:", path)
    Database(db_path=path)
print("Migrated", len(paths), "database(s)")
print(json.dumps(repair_runtime_data_ownership(), indent=2))
PY

echo ""
echo "[7/7] Reload systemd and restart ${SERVICE}..."
systemctl daemon-reload
systemctl restart "${SERVICE}"
sleep 3

if ! systemctl is-active --quiet "${SERVICE}"; then
  echo "ERROR: ${SERVICE} failed to start." >&2
  systemctl status "${SERVICE}" --no-pager || true
  journalctl -u "${SERVICE}" -n 50 --no-pager || true
  exit 1
fi

NEW_WD="$(systemctl show "${SERVICE}" -p WorkingDirectory --value 2>/dev/null || true)"
echo "WorkingDirectory now: ${NEW_WD}"
systemctl status "${SERVICE}" --no-pager | head -20

echo ""
echo "Local health check..."
HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:5001/ || echo '000')"
echo "HTTP 127.0.0.1:5001/ -> ${HTTP_CODE}"

echo ""
echo "=== Deploy complete ==="
echo "Verify: https://ledger.nexallegal.co.uk/office-account"
echo "Upload statement — VAT column between Source and Cleared."
