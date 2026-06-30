#!/usr/bin/env bash
# Deploy Ledger ops-secret fix and verify backup health authentication.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/nexal-ledger}"
SERVICE="${SERVICE:-nexal-ledger}"
LEDGER_PORT="${LEDGER_PORT:-5001}"

cd "${APP_DIR}"
git pull origin main
bash scripts/configure_ops_secret.sh

secret="$(grep '^NEXAL_OPS_SECRET=' /etc/nexal-ledger.env | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
health_code="$(curl -s -o /dev/null -w '%{http_code}' \
  -H "X-Nexal-Ops-Secret: ${secret}" \
  "http://127.0.0.1:${LEDGER_PORT}/api/ops/backup-health")"

echo "Backup health HTTP status: ${health_code}"
if [[ "${health_code}" != "200" ]]; then
  echo "Backup health authentication failed. Ensure Vercel NEXAL_OPS_SECRET matches /etc/nexal-ledger.env." >&2
  exit 1
fi
