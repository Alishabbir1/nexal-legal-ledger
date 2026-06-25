#!/usr/bin/env bash
# Deploy SSO runtime-path fix to Ledger VPS (inline repair — no migration scripts).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE="${SERVICE:-nexal-ledger}"
NEXAL_DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"

echo "=== Nexal Ledger — SSO runtime path deploy ==="
echo "App directory: ${APP_DIR}"
echo "Data directory: ${NEXAL_DATA_DIR}"

cd "${APP_DIR}"
export PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export NEXAL_DATA_DIR

git fetch origin main && git checkout main && git pull origin main
echo "HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"

python3 -m pytest tests/test_runtime_paths.py tests/test_phase4b_sso.py tests/test_phase4e_sso_only.py tests/test_phase4c_integration.py -q --tb=short

echo ""
echo "Verify systemd NEXAL_DATA_DIR:"
systemctl show "${SERVICE}" -p Environment --value | tr ' ' '\n' | grep NEXAL_DATA_DIR || {
  echo "ERROR: NEXAL_DATA_DIR not set on ${SERVICE}. Add to unit file before restart."
  exit 1
}

sudo systemctl restart "${SERVICE}"
sleep 2
systemctl is-active "${SERVICE}"

echo ""
echo "Deploy complete. Test Launch Application from the portal for sunthessmunir@gmail.com."