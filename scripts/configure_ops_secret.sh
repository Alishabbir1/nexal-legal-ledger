#!/usr/bin/env bash
# Configure production secrets on the Ledger VPS (ops, Flask session, SSO sync).
# Run on the VPS as root. Requires python3.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ENV_FILE="${NEXAL_LEDGER_ENV_FILE:-/etc/nexal-ledger.env}"
SERVICE="${SERVICE:-nexal-ledger}"
SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
DROPIN_FILE="${DROPIN_DIR}/99-nexal-env.conf"
LEDGER_PORT="${LEDGER_PORT:-5001}"
APP_DIR="${APP_DIR:-/opt/nexal-ledger}"
DEV_FLASK_SECRET="sra-compliant-secret-key-change-in-production"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root on the Ledger VPS." >&2
  exit 1
fi

export ENV_FILE SERVICE SERVICE_FILE DROPIN_DIR DROPIN_FILE DEV_FLASK_SECRET APP_DIR LEDGER_PORT

exec python3 -u "${SCRIPT_DIR}/configure_production_env.py"
