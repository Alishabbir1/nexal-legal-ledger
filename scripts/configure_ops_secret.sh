#!/usr/bin/env bash
# Configure production secrets on the Ledger VPS (ops, Flask session, SSO sync).
# Run on the VPS as root. Requires openssl.
set -euo pipefail

ENV_FILE="${NEXAL_LEDGER_ENV_FILE:-/etc/nexal-ledger.env}"
SERVICE="${SERVICE:-nexal-ledger}"
SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
LEDGER_PORT="${LEDGER_PORT:-5001}"
APP_DIR="${APP_DIR:-/opt/nexal-ledger}"
DEV_FLASK_SECRET="sra-compliant-secret-key-change-in-production"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root on the Ledger VPS." >&2
  exit 1
fi

mkdir -p "$(dirname "${ENV_FILE}")"
touch "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

grep -q '^NEXAL_PRODUCTION=' "${ENV_FILE}" || echo 'NEXAL_PRODUCTION=true' >> "${ENV_FILE}"

read_env_file_var() {
  grep -E "^${1}=" "${ENV_FILE}" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true
}

read_systemd_var() {
  local key="$1"
  if [[ ! -f "${SERVICE_FILE}" ]]; then
    return 0
  fi
  grep -E "^Environment=.*${key}=" "${SERVICE_FILE}" 2>/dev/null | tail -n1 | sed -n "s/.*${key}=\([^\"' ]*\).*/\1/p" || true
}

write_env_var() {
  local key="$1"
  local val="$2"
  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${ENV_FILE}"
  else
    echo "${key}=${val}" >> "${ENV_FILE}"
  fi
}

is_valid_secret() {
  local value="$1"
  local min_len="${2:-16}"
  [[ -n "${value}" && "${#value}" -ge "${min_len}" ]]
}

ensure_ops_secret() {
  local secret existing
  existing="$(read_env_file_var NEXAL_OPS_SECRET)"

  if is_valid_secret "${existing}" 16; then
    secret="${existing}"
    echo "Using existing NEXAL_OPS_SECRET from ${ENV_FILE}."
  else
    if [[ -n "${NEXAL_OPS_SECRET:-}" && "${#NEXAL_OPS_SECRET}" -ge 16 ]]; then
      secret="${NEXAL_OPS_SECRET}"
      echo "Using NEXAL_OPS_SECRET from environment."
    else
      secret="$(openssl rand -hex 32)"
      echo "Generated new NEXAL_OPS_SECRET."
    fi
    write_env_var NEXAL_OPS_SECRET "${secret}"
  fi

  if [[ -f "${SERVICE_FILE}" ]] && ! grep -q '^EnvironmentFile=' "${SERVICE_FILE}"; then
    sed -i "/^\[Service\]/a EnvironmentFile=-${ENV_FILE}" "${SERVICE_FILE}"
    echo "Added EnvironmentFile=-${ENV_FILE} to ${SERVICE_FILE}."
  fi

  if [[ -f "${SERVICE_FILE}" ]]; then
    local inline_secret
    inline_secret="$(read_systemd_var NEXAL_OPS_SECRET)"
    if is_valid_secret "${inline_secret}" 16 && ! is_valid_secret "$(read_env_file_var NEXAL_OPS_SECRET)" 16; then
      write_env_var NEXAL_OPS_SECRET "${inline_secret}"
      secret="${inline_secret}"
      echo "Synced NEXAL_OPS_SECRET from systemd Environment= into ${ENV_FILE}."
    fi
  fi

  echo "${secret}"
}

ensure_flask_secret_key() {
  local existing systemd_val
  existing="$(read_env_file_var FLASK_SECRET_KEY)"

  if is_valid_secret "${existing}" 16 && [[ "${existing}" != "${DEV_FLASK_SECRET}" ]]; then
    echo "Using existing FLASK_SECRET_KEY from ${ENV_FILE}."
    return 0
  fi

  systemd_val="$(read_systemd_var FLASK_SECRET_KEY)"
  if is_valid_secret "${systemd_val}" 16 && [[ "${systemd_val}" != "${DEV_FLASK_SECRET}" ]]; then
    write_env_var FLASK_SECRET_KEY "${systemd_val}"
    echo "Synced FLASK_SECRET_KEY from systemd Environment= into ${ENV_FILE}."
    return 0
  fi

  systemd_val="$(read_systemd_var SECRET_KEY)"
  if is_valid_secret "${systemd_val}" 16 && [[ "${systemd_val}" != "${DEV_FLASK_SECRET}" ]]; then
    write_env_var FLASK_SECRET_KEY "${systemd_val}"
    echo "Synced SECRET_KEY from systemd into FLASK_SECRET_KEY in ${ENV_FILE}."
    return 0
  fi

  write_env_var FLASK_SECRET_KEY "$(openssl rand -hex 32)"
  echo "Generated new FLASK_SECRET_KEY in ${ENV_FILE}."
}

ensure_sso_secret_key() {
  local existing systemd_val
  existing="$(read_env_file_var SSO_SECRET_KEY)"

  if is_valid_secret "${existing}" 16; then
    echo "Using existing SSO_SECRET_KEY from ${ENV_FILE}."
    return 0
  fi

  systemd_val="$(read_systemd_var SSO_SECRET_KEY)"
  if is_valid_secret "${systemd_val}" 16; then
    write_env_var SSO_SECRET_KEY "${systemd_val}"
    echo "Synced SSO_SECRET_KEY from systemd Environment= into ${ENV_FILE}."
    return 0
  fi

  systemd_val="$(read_systemd_var NEXAL_SSO_SECRET)"
  if is_valid_secret "${systemd_val}" 16; then
    write_env_var SSO_SECRET_KEY "${systemd_val}"
    echo "Synced NEXAL_SSO_SECRET from systemd into SSO_SECRET_KEY in ${ENV_FILE}."
    return 0
  fi

  echo "ERROR: SSO_SECRET_KEY is missing from ${ENV_FILE} and systemd." >&2
  echo "Set SSO_SECRET_KEY in ${ENV_FILE} to match Portal LEDGER_SSO_SECRET, then re-run." >&2
  exit 1
}

validate_production_secrets_python() {
  if [[ ! -d "${APP_DIR}" ]]; then
    echo "Skipping Python validation (APP_DIR ${APP_DIR} not found)."
    return 0
  fi

  NEXAL_LEDGER_ENV_FILE="${ENV_FILE}" APP_DIR="${APP_DIR}" python3 <<'PY'
import os
import sys

app_dir = os.environ["APP_DIR"]
sys.path.insert(0, app_dir)
os.environ.setdefault("NEXAL_PRODUCTION", "true")

from nexal_platform.ops_secret import bootstrap_ledger_env, get_expected_ops_secret
from nexal_platform.production_secrets import validate_production_secrets

bootstrap_ledger_env()
validate_production_secrets(
    sso_secret=os.environ.get("SSO_SECRET_KEY") or os.environ.get("NEXAL_SSO_SECRET"),
    flask_secret=os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY"),
    ops_secret=get_expected_ops_secret() or None,
)
print("Production secret validation passed.")
PY
}

ops_secret="$(ensure_ops_secret)"
ensure_flask_secret_key
ensure_sso_secret_key
validate_production_secrets_python

systemctl daemon-reload
systemctl restart "${SERVICE}"
systemctl is-active --quiet "${SERVICE}"

root_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${LEDGER_PORT}/" || true)"
health_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
  -H "X-Nexal-Ops-Secret: ${ops_secret}" \
  "http://127.0.0.1:${LEDGER_PORT}/api/ops/backup-health" || true)"

echo ""
echo "Ledger service restarted."
echo "Local root HTTP status: ${root_code:-unavailable}"
echo "Local backup-health HTTP status: ${health_code:-unavailable}"
echo ""
echo "Set the SAME value on Vercel (Portal production):"
echo "  NEXAL_OPS_SECRET=${ops_secret}"
echo ""

if [[ "${health_code}" != "200" ]]; then
  echo "WARNING: Local backup-health did not return HTTP 200. Check: journalctl -u ${SERVICE} -n 50 --no-pager" >&2
  exit 1
fi
