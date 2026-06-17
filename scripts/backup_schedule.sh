#!/usr/bin/env bash
# Nexal Legal Ledger — cron-friendly backup scheduler
# Install on VPS (example):
#   0 2 * * *  /opt/nexal-legal/scripts/backup_schedule.sh daily
#   0 3 * * 0  /opt/nexal-legal/scripts/backup_schedule.sh weekly
#   0 4 1 * *  /opt/nexal-legal/scripts/backup_schedule.sh monthly
set -euo pipefail

SCHEDULE="${1:-daily}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export NEXAL_DATA_DIR="${NEXAL_DATA_DIR:-/var/lib/nexal-legal}"
export NEXAL_BACKUP_DIR="${NEXAL_BACKUP_DIR:-${NEXAL_DATA_DIR}/backups}"

cd "$ROOT"
python3 scripts/backup_all.py --schedule "$SCHEDULE"
