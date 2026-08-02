#!/usr/bin/env bash
# Restore production Ledger — same deploy as bank statement upload (PHASE4B).
# Ali runs TWO commands on VPS (as root):
#   curl -fsSL https://raw.githubusercontent.com/Alishabbir1/nexal-legal-ledger/main/scripts/deploy_ledger_production.sh | bash
#   curl -sI https://ledger.nexallegal.co.uk/ | head -3
#
# Canonical production layout:
#   APP_DIR:  /opt/nexal-legal-ledger   (sync user reads here — NOT /root/)
#   DATA_DIR: /var/lib/nexal-legal
#   SERVICE:  nexal-ledger
#   USER:     sync
set -euo pipefail

SERVICE="${SERVICE:-nexal-ledger}"
APP_DIR="${APP_DIR:-/opt/nexal-legal-ledger}"
DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"
GITHUB_REPO="${NEXAL_GITHUB_REPO:-https://github.com/Alishabbir1/nexal-legal-ledger.git}"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
DROPIN_FILE="${DROPIN_DIR}/working-directory.conf"
EXPECTED_MARKER="VAT-COLUMN-ENABLED-v2"
MIN_VAT_COMMIT="83674ff"

echo "=== Nexal Ledger — production deploy ==="

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: Run as root on the VPS." >&2
  exit 1
fi

resolve_service_user() {
  local u
  u="$(systemctl show "${SERVICE}" -p User --value 2>/dev/null || true)"
  if [[ -n "${u}" && "${u}" != "root" && "${u}" != "0" ]]; then
    echo "${u}"
    return 0
  fi
  echo "sync"
}

ensure_app_dir() {
  if [[ -d "${APP_DIR}/.git" ]]; then
    return 0
  fi
  echo "Cloning ${GITHUB_REPO} -> ${APP_DIR}"
  mkdir -p "$(dirname "${APP_DIR}")"
  git clone "${GITHUB_REPO}" "${APP_DIR}"
}

SERVICE_USER="$(resolve_service_user)"
ensure_app_dir

echo "Service user: ${SERVICE_USER}"
echo "APP_DIR: ${APP_DIR}"
echo "DATA_DIR: ${DATA_DIR}"

CURRENT_WD="$(systemctl show "${SERVICE}" -p WorkingDirectory --value 2>/dev/null || true)"
echo "Previous WorkingDirectory: ${CURRENT_WD:-unknown}"

echo ""
echo "[1/8] Remove broken WorkingDirectory overrides..."
if [[ -d "${DROPIN_DIR}" ]]; then
  grep -rl 'WorkingDirectory=/opt/nexal-legal[^-]' "${DROPIN_DIR}" 2>/dev/null | while read -r f; do
    echo "Removing bad override: ${f}"
    rm -f "${f}"
  done || true
  grep -rl 'WorkingDirectory=/root/' "${DROPIN_DIR}" 2>/dev/null | while read -r f; do
    echo "Removing /root override: ${f}"
    rm -f "${f}"
  done || true
fi

echo ""
echo "[2/8] Install WorkingDirectory=${APP_DIR}..."
mkdir -p "${DROPIN_DIR}"
cat > "${DROPIN_FILE}" <<EOF
[Service]
WorkingDirectory=${APP_DIR}
EOF
cat "${DROPIN_FILE}"

echo ""
echo "[3/8] Ensure ${SERVICE_USER} can read ${APP_DIR}..."
chown -R root:"${SERVICE_USER}" "${APP_DIR}"
chmod -R u=rwX,g=rX,o=rX "${APP_DIR}"

echo ""
echo "[4/8] git pull origin main..."
cd "${APP_DIR}"
git fetch origin main
git checkout main
git pull origin main
HEAD="$(git rev-parse --short HEAD)"
echo "HEAD: ${HEAD} — $(git log -1 --format=%s)"
if ! git merge-base --is-ancestor "${MIN_VAT_COMMIT}" HEAD 2>/dev/null; then
  echo "WARNING: ${MIN_VAT_COMMIT} (VAT module) not in history — check branch." >&2
fi

echo ""
echo "[5/8] Clear stale bytecode..."
find "${APP_DIR}" -name "*.pyc" -delete 2>/dev/null || true
find "${APP_DIR}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo ""
echo "[6/8] Verify VAT templates..."
if ! grep -q "${EXPECTED_MARKER}" "${APP_DIR}/templates/office_import_review.html"; then
  echo "ERROR: VAT module template missing (${EXPECTED_MARKER})." >&2
  exit 1
fi
test -f "${APP_DIR}/lib/vat.py" || { echo "ERROR: lib/vat.py missing" >&2; exit 1; }
echo "VAT module files OK"

echo ""
echo "[7/8] Migrate databases + repair ownership for ${SERVICE_USER}..."
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
echo "[8/8] daemon-reload + restart ${SERVICE}..."
systemctl daemon-reload
systemctl restart "${SERVICE}"
sleep 4

if ! systemctl is-active --quiet "${SERVICE}"; then
  echo "ERROR: ${SERVICE} failed to start." >&2
  systemctl status "${SERVICE}" --no-pager || true
  journalctl -u "${SERVICE}" -n 60 --no-pager || true
  exit 1
fi

echo "WorkingDirectory: $(systemctl show "${SERVICE}" -p WorkingDirectory --value)"
systemctl status "${SERVICE}" --no-pager | head -15
HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:5001/ || echo '000')"
echo "Local http://127.0.0.1:5001/ -> HTTP ${HTTP_CODE}"
echo ""
echo "=== Done. Site should be live at https://ledger.nexallegal.co.uk/ ==="
