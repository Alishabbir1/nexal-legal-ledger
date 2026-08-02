#!/usr/bin/env bash
# Bootstrap VAT module deploy on VPS (IONOS console or SSH):
#   curl -fsSL https://raw.githubusercontent.com/Alishabbir1/nexal-legal-ledger/main/scripts/bootstrap_deploy_vat.sh | bash
set -euo pipefail

GITHUB_REPO="${NEXAL_GITHUB_REPO:-https://github.com/Alishabbir1/nexal-legal-ledger.git}"

find_repo() {
  local candidate wd
  wd="$(systemctl show nexal-ledger -p WorkingDirectory --value 2>/dev/null || true)"
  if [[ -n "${wd}" && "${wd}" != "n/a" && -d "${wd}/.git" ]]; then
    echo "${wd}"
    return 0
  fi
  for candidate in \
    "${NEXAL_LEDGER_REPO:-}" \
    "/opt/nexal-ledger" \
    "/opt/nexal-legal-ledger" \
    "/root/nexal-legal-ledger"; do
    if [[ -n "${candidate}" && -d "${candidate}/.git" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

REPO="$(find_repo || true)"
if [[ -z "${REPO}" ]]; then
  REPO="/opt/nexal-ledger"
  echo "== Cloning ledger repository to ${REPO} =="
  git clone "${GITHUB_REPO}" "${REPO}"
fi

cd "${REPO}"
git fetch origin main
git checkout main
git reset --hard origin/main

if [[ ! -f scripts/deploy_vat_module.sh ]]; then
  echo "ERROR: deploy_vat_module.sh missing after sync." >&2
  exit 1
fi

exec bash scripts/deploy_vat_module.sh
