#!/usr/bin/env bash
# Configure NEXAL_OPS_SECRET on the Ledger VPS for Portal backup health API.
# Run on the VPS as root. Requires openssl.
set -euo pipefail

ENV_FILE="${NEXAL_LEDGER_ENV_FILE:-/etc/nexal-ledger.env}"
SERVICE="${SERVICE:-nexal-ledger}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root on the Ledger VPS." >&2
  exit 1
fi

mkdir -p "$(dirname "${ENV_FILE}")"
touch "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

existing="$(grep -E '^NEXAL_OPS_SECRET=' "${ENV_FILE}" | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"

if [[ -n "${existing}" ]]; then
  secret="${existing}"
  echo "Using existing NEXAL_OPS_SECRET from ${ENV_FILE}."
else
  if [[ -n "${NEXAL_OPS_SECRET:-}" ]]; then
    secret="${NEXAL_OPS_SECRET}"
    echo "Using NEXAL_OPS_SECRET from environment."
  else
    secret="$(openssl rand -hex 32)"
    echo "Generated new NEXAL_OPS_SECRET."
  fi
  if grep -q '^NEXAL_OPS_SECRET=' "${ENV_FILE}"; then
    sed -i "s|^NEXAL_OPS_SECRET=.*|NEXAL_OPS_SECRET=${secret}|" "${ENV_FILE}"
  else
    echo "NEXAL_OPS_SECRET=${secret}" >> "${ENV_FILE}"
  fi
fi

if ! grep -q '^EnvironmentFile=' "/etc/systemd/system/${SERVICE}.service" 2>/dev/null; then
  echo "Ensure systemd loads ${ENV_FILE}, e.g.:"
  echo "  EnvironmentFile=${ENV_FILE}"
fi

systemctl daemon-reload
systemctl restart "${SERVICE}"
systemctl is-active --quiet "${SERVICE}"

health_code="$(curl -s -o /dev/null -w '%{http_code}' \
  -H "X-Nexal-Ops-Secret: ${secret}" \
  "http://127.0.0.1:5000/api/ops/backup-health" || true)"

echo ""
echo "Ledger service restarted."
echo "Local health check HTTP status: ${health_code:-unavailable}"
echo ""
echo "Set the SAME value on Vercel (Portal production):"
echo "  NEXAL_OPS_SECRET=${secret}"
echo ""
echo "After updating Vercel, Operations → Backups should show Ledger health as ready."
