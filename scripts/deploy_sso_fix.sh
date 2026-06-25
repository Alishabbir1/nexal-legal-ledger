#!/usr/bin/env bash
# Deploy SSO tenant repair + logout redirect fixes to Ledger VPS.
set -euo pipefail

APP_DIR="${APP_DIR:-/root/nexal-legal-ledger}"
SERVICE="${SERVICE:-nexal-ledger}"
PORTAL_FIRM_ID="${PORTAL_FIRM_ID:-498205b5-0d17-453c-a0de-e507955e94fb}"

echo "=== Nexal Ledger — SSO + logout deploy ==="
cd "${APP_DIR}"
git fetch origin main && git checkout main && git pull origin main
echo "HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"

python3 -m pytest tests/test_phase4b_sso.py tests/test_phase4e_sso_only.py -q --tb=short

echo ""
echo "Verify portal firm link (optional repair):"
python3 scripts/link_portal_firm.py \
  --portal-firm-id "${PORTAL_FIRM_ID}" \
  --verify-only || true

sudo systemctl restart "${SERVICE}"
sleep 2
systemctl is-active "${SERVICE}"

echo ""
echo "Logout redirect:"
curl -sI "https://ledger.nexallegal.co.uk/logout" | tr -d '\r' | grep -i location

echo ""
echo "Deploy complete. Test Launch Application from the portal for sunthessmunir@gmail.com."
