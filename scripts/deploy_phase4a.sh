#!/usr/bin/env bash
# Nexal Legal — Phase 4A VPS deployment script
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

: "${NEXAL_DATA_DIR:=/var/lib/nexal-legal}"

echo "Nexal Legal — Phase 4A Deployment"
echo "================================="
echo "App directory: ${APP_DIR}"
echo "Data directory: ${NEXAL_DATA_DIR}"

mkdir -p "${NEXAL_DATA_DIR}"
export NEXAL_DATA_DIR

cd "${APP_DIR}"

echo ""
echo "[1/4] Installing Python dependencies..."
python3 -m pip install -r requirements.txt

echo ""
echo "[2/4] Ensuring template database..."
python3 -c "from nexal_platform.template import ensure_template_database; print(ensure_template_database())"

echo ""
echo "[3/4] Running legacy migration (non-destructive)..."
if [ -f "${APP_DIR}/solicitor_ledger.db" ]; then
  python3 phase4a_migrate.py \
    --legacy-path "${APP_DIR}/solicitor_ledger.db" \
    --firm-code FIRM000 \
    --firm-name "Legacy Firm" \
    --slug legacy || true
else
  echo "No legacy solicitor_ledger.db in app directory — skipping migration."
fi

echo ""
echo "[4/4] Running Phase 4A validation..."
python3 phase4a_test.py

echo ""
echo "[5/5] Running Phase 4B/4C integration tests..."
python3 -m pytest tests/test_phase4b_sso.py tests/test_phase4c_integration.py -q

echo ""
echo "Phase 4A deployment preparation complete."
echo "Restart the ledger service to apply any code updates:"
echo "  sudo systemctl restart nexal-ledger"
