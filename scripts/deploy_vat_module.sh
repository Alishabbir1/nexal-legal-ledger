#!/usr/bin/env bash
# Deploy VAT module (and any pending main) to the Ledger VPS.
# Run ON the VPS as root. Auto-detects the directory gunicorn actually uses.
#
# Usage:
#   bash scripts/deploy_vat_module.sh
#   APP_DIR=/opt/nexal-ledger bash scripts/deploy_vat_module.sh
#   curl -fsSL .../bootstrap_deploy_vat.sh | bash
set -euo pipefail

SERVICE="${SERVICE:-nexal-ledger}"
EXPECTED_MARKER="VAT-COLUMN-ENABLED-v2"
MIN_COMMIT_PREFIX="83674ff"

echo "=== Nexal Ledger — VAT module deploy ==="
echo "Service: ${SERVICE}"

resolve_working_directory() {
  local wd=""
  wd="$(systemctl show "${SERVICE}" -p WorkingDirectory --value 2>/dev/null || true)"
  if [[ -n "${wd}" && "${wd}" != "n/a" && -d "${wd}" ]]; then
    echo "${wd}"
    return 0
  fi
  local unit=""
  for unit in \
    "/etc/systemd/system/${SERVICE}.service" \
    "/lib/systemd/system/${SERVICE}.service"; do
    if [[ -f "${unit}" ]]; then
      wd="$(grep -E '^WorkingDirectory=' "${unit}" | head -1 | cut -d= -f2- || true)"
      wd="${wd//\"/}"
      if [[ -n "${wd}" && -d "${wd}" ]]; then
        echo "${wd}"
        return 0
      fi
    fi
  done
  return 1
}

find_git_repo() {
  local candidate
  for candidate in \
    "${APP_DIR:-}" \
    "$(resolve_working_directory || true)" \
    "${NEXAL_LEDGER_REPO:-}" \
    "/opt/nexal-ledger" \
    "/opt/nexal-legal-ledger" \
    "/opt/nexal-legal" \
    "/root/nexal-legal-ledger"; do
    [[ -n "${candidate}" ]] || continue
    [[ -d "${candidate}/.git" ]] || continue
    echo "${candidate}"
    return 0
  done
  return 1
}

has_vat_template() {
  local dir="$1"
  [[ -f "${dir}/templates/office_import_review.html" ]] \
    && grep -q "${EXPECTED_MARKER}" "${dir}/templates/office_import_review.html"
}

SYSTEMD_WD="$(resolve_working_directory || echo "(unknown)")"
REPO="$(find_git_repo || true)"

echo ""
echo "[diag] systemd WorkingDirectory: ${SYSTEMD_WD}"
echo "[diag] git repository found: ${REPO:-NONE}"

if [[ -z "${REPO}" ]]; then
  echo "ERROR: No ledger git repository found." >&2
  exit 1
fi

if [[ "${SYSTEMD_WD}" != "(unknown)" && "${SYSTEMD_WD}" != "${REPO}" ]]; then
  echo ""
  echo "WARNING: systemd WorkingDirectory (${SYSTEMD_WD}) differs from git repo (${REPO})."
  echo "         gunicorn serves code from WorkingDirectory — deploy must target that path."
  if [[ -d "${SYSTEMD_WD}/.git" ]]; then
    REPO="${SYSTEMD_WD}"
    echo "         Using systemd WorkingDirectory as APP_DIR: ${REPO}"
  elif [[ -d "${SYSTEMD_WD}" ]]; then
    echo "ERROR: ${SYSTEMD_WD} is not a git repo but is the live WorkingDirectory." >&2
    echo "       Either:" >&2
    echo "         1) git clone https://github.com/Alishabbir1/nexal-legal-ledger.git ${SYSTEMD_WD}" >&2
    echo "         2) update systemd WorkingDirectory to ${REPO} and daemon-reload" >&2
    exit 1
  fi
fi

cd "${REPO}"
APP_DIR="${REPO}"
echo ""
echo "Deploying from: ${APP_DIR}"

echo ""
echo "[1/8] Pull latest main..."
git fetch origin main
git checkout main
git pull origin main
HEAD="$(git rev-parse --short HEAD)"
echo "HEAD: ${HEAD} — $(git log -1 --format=%s)"

if [[ "${HEAD}" != "${MIN_COMMIT_PREFIX}"* ]]; then
  echo "WARNING: Expected commit starting with ${MIN_COMMIT_PREFIX}, got ${HEAD}"
fi

echo ""
echo "[2/8] Verify VAT template on disk..."
if ! has_vat_template "${APP_DIR}"; then
  echo "ERROR: ${EXPECTED_MARKER} not found in templates/office_import_review.html" >&2
  exit 1
fi
grep -n "${EXPECTED_MARKER}" "${APP_DIR}/templates/office_import_review.html" | head -3

echo ""
echo "[3/8] Clear stale Python bytecode..."
find "${APP_DIR}" -name "*.pyc" -delete
find "${APP_DIR}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo ""
echo "[4/8] Show gunicorn processes..."
ps aux | grep -E '[g]unicorn|[a]pp:app|[a]pp.py' || echo "(no matching processes yet)"

echo ""
echo "[5/8] Run database migrations (additive only)..."
export NEXAL_DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"
python3 <<'PY'
import glob
import os
from database import Database

data_root = os.environ.get("NEXAL_DATA_DIR", "/var/lib/nexal-legal")
paths = glob.glob(f"{data_root}/tenants/*/solicitor_ledger.db")
legacy = f"{data_root}/solicitor_ledger.db"
if os.path.isfile(legacy):
    paths.insert(0, legacy)
template = f"{data_root}/templates/solicitor_ledger.db"
if os.path.isfile(template):
    paths.append(template)
if not paths:
    print("No tenant databases found under", data_root)
else:
    for path in paths:
        print("Migrating:", path)
        Database(db_path=path)
    print("Done —", len(paths), "database(s)")
PY

echo ""
echo "[6/8] Hard restart ${SERVICE}..."
systemctl daemon-reload
systemctl restart "${SERVICE}"
sleep 3
systemctl is-active "${SERVICE}"
systemctl status "${SERVICE}" --no-pager | head -20

echo ""
echo "[7/8] Confirm live WorkingDirectory after restart..."
resolve_working_directory || true

echo ""
echo "[8/8] Post-deploy checks..."
if [[ -f "${APP_DIR}/lib/vat.py" ]]; then
  echo "lib/vat.py: present"
else
  echo "ERROR: lib/vat.py missing in ${APP_DIR}" >&2
  exit 1
fi
if [[ -f "${APP_DIR}/templates/vat_setup.html" ]]; then
  echo "templates/vat_setup.html: present"
else
  echo "ERROR: templates/vat_setup.html missing" >&2
  exit 1
fi

echo ""
echo "=== Deploy complete ==="
echo "Verify in browser (after login):"
echo "  https://ledger.nexallegal.co.uk/office-account"
echo "  https://ledger.nexallegal.co.uk/office-account/vat/setup"
echo "  Upload a statement — VAT column should appear between Source and Cleared."
