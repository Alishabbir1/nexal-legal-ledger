#!/usr/bin/env bash
# Repair Serene Solicitors SSO on production VPS after legacy migration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE="${SERVICE:-nexal-ledger}"
DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"

echo "=== Serene Solicitors — SSO repair deploy ==="
echo "App directory: ${APP_DIR}"
echo "Data directory: ${DATA_DIR}"

cd "${APP_DIR}"
export PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export NEXAL_DATA_DIR="${DATA_DIR}"

git fetch origin main
git reset --hard origin/main
echo "HEAD: $(git rev-parse --short HEAD) — $(git log -1 --format=%s)"

python3 -m pytest \
  tests/test_serene_production_sso.py \
  tests/test_legacy_tenant_import.py \
  tests/test_runtime_paths.py \
  tests/test_phase4b_sso.py \
  -q --tb=short

echo ""
echo "== Repairing Serene tenant path and verifying SSO =="
python3 scripts/repair_serene_sso.py

echo ""
echo "== Restarting ${SERVICE} =="
systemctl restart "${SERVICE}"
sleep 2
systemctl is-active "${SERVICE}"

echo ""
echo "SUCCESS: Serene SSO repair deployed."
echo "Log into Portal as Smalik34@hotmail.co.uk and Launch Application to verify."
