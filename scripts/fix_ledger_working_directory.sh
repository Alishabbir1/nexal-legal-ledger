#!/usr/bin/env bash
# Fix nexal-ledger.service WorkingDirectory when it points at a missing path
# (e.g. /opt/nexal-legal) while code lives in /root/nexal-legal-ledger.
#
# Run on VPS as root (IONOS console or SSH):
#   curl -fsSL https://raw.githubusercontent.com/Alishabbir1/nexal-legal-ledger/main/scripts/fix_ledger_working_directory.sh | bash
#
# Or after git pull:
#   bash scripts/fix_ledger_working_directory.sh
set -euo pipefail

SERVICE="${SERVICE:-nexal-ledger}"
APP_DIR="${APP_DIR:-/root/nexal-legal-ledger}"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
DROPIN_FILE="${DROPIN_DIR}/working-directory.conf"
EXPECTED_MARKER="VAT-COLUMN-ENABLED-v2"

echo "=== Nexal Ledger — fix WorkingDirectory ==="
echo "Service: ${SERVICE}"
echo "Target APP_DIR: ${APP_DIR}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "ERROR: ${APP_DIR} is not a git repository." >&2
  exit 1
fi

CURRENT_WD="$(systemctl show "${SERVICE}" -p WorkingDirectory --value 2>/dev/null || true)"
echo "Current WorkingDirectory: ${CURRENT_WD:-unknown}"

if [[ "${CURRENT_WD}" == "${APP_DIR}" ]]; then
  echo "WorkingDirectory already correct."
else
  echo ""
  echo "Installing systemd drop-in: ${DROPIN_FILE}"
  mkdir -p "${DROPIN_DIR}"
  cat > "${DROPIN_FILE}" <<EOF
[Service]
WorkingDirectory=${APP_DIR}
EOF
  echo "Wrote:"
  cat "${DROPIN_FILE}"
fi

echo ""
echo "Reloading systemd and restarting ${SERVICE}..."
systemctl daemon-reload
systemctl restart "${SERVICE}"
sleep 3

if ! systemctl is-active --quiet "${SERVICE}"; then
  echo "ERROR: ${SERVICE} failed to start." >&2
  systemctl status "${SERVICE}" --no-pager || true
  journalctl -u "${SERVICE}" -n 40 --no-pager || true
  exit 1
fi

NEW_WD="$(systemctl show "${SERVICE}" -p WorkingDirectory --value 2>/dev/null || true)"
echo "New WorkingDirectory: ${NEW_WD}"
systemctl status "${SERVICE}" --no-pager | head -25

echo ""
echo "Pulling latest code and clearing bytecode cache..."
cd "${APP_DIR}"
git fetch origin main
git checkout main
git pull origin main
echo "HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"

find "${APP_DIR}" -name "*.pyc" -delete 2>/dev/null || true
find "${APP_DIR}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

if grep -q "${EXPECTED_MARKER}" "${APP_DIR}/templates/office_import_review.html"; then
  echo "VAT template marker OK: ${EXPECTED_MARKER}"
else
  echo "WARNING: ${EXPECTED_MARKER} not found — code may still be outdated." >&2
fi

if [[ -f "${APP_DIR}/scripts/deploy_vat_module.sh" ]]; then
  echo ""
  echo "Running deploy_vat_module.sh (migrations + final restart)..."
  bash "${APP_DIR}/scripts/deploy_vat_module.sh"
else
  echo ""
  echo "Restarting once more after pull..."
  systemctl restart "${SERVICE}"
  systemctl is-active "${SERVICE}"
fi

echo ""
echo "=== Fix complete ==="
echo "Verify in browser: https://ledger.nexallegal.co.uk/office-account"
echo "Upload a statement — VAT column should appear between Source and Cleared."
