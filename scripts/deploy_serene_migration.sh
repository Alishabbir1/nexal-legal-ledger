#!/usr/bin/env bash
# One-time Serene Solicitors production migration on the Ledger VPS.
set -euo pipefail

LEGACY_PATH="${1:-/tmp/serene_solicitor_ledger.db}"
REPO="${NEXAL_LEDGER_REPO:-/opt/nexal-legal-ledger}"
DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"

if [[ ! -f "${LEGACY_PATH}" ]]; then
  echo "Legacy database not found: ${LEGACY_PATH}" >&2
  echo "Upload desktop DB first, e.g. scp %LOCALAPPDATA%\\SolicitorLedger\\solicitor_ledger.db root@VPS:${LEGACY_PATH}" >&2
  exit 1
fi

cd "${REPO}"
export NEXAL_DATA_DIR="${DATA_DIR}"

echo "== Dry run validation =="
python3 scripts/migrate_serene_production.py --legacy-path "${LEGACY_PATH}" --dry-run

echo "== Applying migration =="
python3 scripts/migrate_serene_production.py --legacy-path "${LEGACY_PATH}" --apply

echo "== Restarting ledger service =="
systemctl restart nexal-ledger || service nexal-ledger restart

echo "Serene production migration complete. Launch Application from the Portal to verify."
