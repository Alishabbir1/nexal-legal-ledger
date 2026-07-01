#!/usr/bin/env bash
# Bootstrap Serene SSO repair on VPS (curl | bash):
#   curl -fsSL https://raw.githubusercontent.com/Alishabbir1/nexal-legal-ledger/main/scripts/bootstrap_serene_sso_fix.sh | bash
set -euo pipefail

GITHUB_REPO="${NEXAL_GITHUB_REPO:-https://github.com/Alishabbir1/nexal-legal-ledger.git}"

find_repo() {
  for candidate in \
    "${NEXAL_LEDGER_REPO:-}" \
    "/root/nexal-legal-ledger" \
    "/opt/nexal-legal-ledger" \
    "/opt/nexal-ledger"; do
    if [[ -n "${candidate}" && -d "${candidate}/.git" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

REPO="$(find_repo || true)"
if [[ -z "${REPO}" ]]; then
  REPO="/root/nexal-legal-ledger"
  echo "== Cloning ledger repository to ${REPO} =="
  git clone "${GITHUB_REPO}" "${REPO}"
fi

cd "${REPO}"
git fetch origin main
git reset --hard origin/main

if [[ ! -f scripts/deploy_serene_sso_fix.sh ]]; then
  echo "ERROR: deploy_serene_sso_fix.sh missing after sync." >&2
  exit 1
fi

exec bash scripts/deploy_serene_sso_fix.sh
