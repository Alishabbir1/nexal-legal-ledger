#!/usr/bin/env bash
# Fix tenant/platform DB ownership after root migration (SSO_DB_ERROR under Gunicorn).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE="${SERVICE:-nexal-ledger}"
DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"

cd "${APP_DIR}"
export PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export NEXAL_DATA_DIR="${DATA_DIR}"

git fetch origin main
git reset --hard origin/main

python3 - <<'PY'
import json
from nexal_platform.migration.tenant_permissions import repair_runtime_data_ownership

print(json.dumps(repair_runtime_data_ownership(), indent=2))
PY

systemctl restart "${SERVICE}"
sleep 2
systemctl is-active "${SERVICE}"
echo "Runtime permissions repaired and ${SERVICE} restarted."
