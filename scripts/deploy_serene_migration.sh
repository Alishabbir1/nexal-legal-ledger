#!/usr/bin/env bash
# One-time Serene Solicitors production migration — run this single script on the VPS.
set -euo pipefail

LEGACY_PATH="${1:-/tmp/serene_solicitor_ledger.db}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"

if [[ ! -f "${LEGACY_PATH}" ]]; then
  echo "ERROR: Legacy database not found: ${LEGACY_PATH}" >&2
  exit 1
fi

cd "${APP_DIR}"
echo "== Updating ledger code =="
git fetch origin main
git reset --hard origin/main

export NEXAL_DATA_DIR="${DATA_DIR}"

echo "== Dry run validation =="
python3 scripts/migrate_serene_production.py --legacy-path "${LEGACY_PATH}" --dry-run

echo "== Applying migration (backup blank tenant, import legacy data, validate) =="
python3 scripts/migrate_serene_production.py --legacy-path "${LEGACY_PATH}" --apply

echo "== Restarting ledger service =="
systemctl restart nexal-ledger

echo ""
echo "SUCCESS: Serene Solicitors production migration complete."
echo "Portal firm: 0343a4a2-5c8e-45ac-a506-61d2dde6fdb3"
echo "Expected: 42 clients | cashbook £36,214.51 | April locked reconciliation"
echo "Log into Portal as Smalik34@hotmail.co.uk and Launch Application to verify."
