#!/usr/bin/env bash
# Deploy logout redirect fix to the Ledger VPS.
# Run ON the VPS as root (or with sudo for systemctl).
set -euo pipefail

APP_DIR="${APP_DIR:-/root/nexal-legal-ledger}"
SERVICE="${SERVICE:-nexal-ledger}"

echo "=== Nexal Ledger — logout redirect deploy ==="
echo "App directory: ${APP_DIR}"
echo "Service: ${SERVICE}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "ERROR: ${APP_DIR} is not a git repository."
  echo "Set APP_DIR to your ledger clone path, e.g.:"
  echo "  APP_DIR=/root/nexal-legal-ledger bash scripts/deploy_logout_redirect.sh"
  exit 1
fi

cd "${APP_DIR}"

echo ""
echo "[1/5] Pull latest main..."
git fetch origin main
git checkout main
git pull origin main
echo "HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"

echo ""
echo "[2/5] Show configured portal URL (systemd)..."
if systemctl cat "${SERVICE}" 2>/dev/null | grep -E 'NEXAL_PORTAL|PORTAL_APP' || true; then
  :
else
  echo "(No NEXAL_PORTAL_URL in unit file — code default applies)"
fi

echo ""
echo "[3/5] Run logout redirect tests..."
python3 -m pytest tests/test_phase4e_sso_only.py tests/test_phase4e_portal_audit.py -q --tb=short

echo ""
echo "[4/5] Restart ${SERVICE}..."
systemctl restart "${SERVICE}"
sleep 2
systemctl is-active "${SERVICE}"

echo ""
echo "[5/5] Verify logout redirect headers..."
for path in /logout /auth/sso/logout; do
  location=$(curl -sI "https://ledger.nexallegal.co.uk${path}" | tr -d '\r' | awk 'tolower($1)=="location:" {print $2}')
  echo "${path} -> ${location:-<missing>}"
  if [[ "${location}" == *"nexallegal.co.uk/portal"* ]]; then
    echo "ERROR: Still redirecting to parked domain path."
    exit 1
  fi
  if [[ "${location}" != "https://nexal-legal.vercel.app/" ]]; then
    echo "WARNING: Expected https://nexal-legal.vercel.app/ got ${location}"
  fi
done

echo ""
echo "Deploy complete."
