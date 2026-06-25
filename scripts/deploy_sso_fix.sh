#!/usr/bin/env bash
# Deploy SSO tenant repair + logout redirect fixes to Ledger VPS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE="${SERVICE:-nexal-ledger}"
PORTAL_FIRM_ID="${PORTAL_FIRM_ID:-498205b5-0d17-453c-a0de-e507955e94fb}"
NEXAL_DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"

echo "=== Nexal Ledger — SSO + logout deploy ==="
echo "App directory: ${APP_DIR}"
echo "Data directory: ${NEXAL_DATA_DIR}"

cd "${APP_DIR}"
export PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export NEXAL_DATA_DIR

git fetch origin main && git checkout main && git pull origin main
echo "HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"

python3 -m pytest tests/test_phase4b_sso.py tests/test_phase4e_sso_only.py tests/test_deploy_cli_scripts.py -q --tb=short

echo ""
echo "Repair portal firm tenant (provision or rebuild workspace/DB):"
python3 scripts/repair_portal_tenant.py \
  --portal-firm-id "${PORTAL_FIRM_ID}" \
  --name "new" \
  --owner-email sunthessmunir@gmail.com \
  --portal-user-id 2cbf9a7d-2f8f-4c4a-9d64-fd7a24d363cc \
  --portal-customer-id 7a0a8a6e-dfc2-444e-9bd0-10e13af27035 \
  --subscription-tier essential

sudo systemctl restart "${SERVICE}"
sleep 2
systemctl is-active "${SERVICE}"

echo ""
echo "Logout redirect:"
curl -sI "https://ledger.nexallegal.co.uk/logout" | tr -d '\r' | grep -i location

echo ""
echo "Deploy complete. Test Launch Application from the portal for sunthessmunir@gmail.com."
