#!/usr/bin/env bash
# Deprecated wrapper — use deploy_ledger_production.sh instead.
# Kept so old curl links still work.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/deploy_ledger_production.sh"
