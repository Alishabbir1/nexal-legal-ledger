# Phase 5.4 — Backup Architecture (Ledger)

See also: Portal copy at `nexal-legal/PHASE_5_4_BACKUP_ARCHITECTURE.md`.

## Ledger backup module

Implementation: `nexal_platform/backup/`

| Module | Role |
|--------|------|
| `config.py` | Paths, retention, compression |
| `snapshot.py` | SQLite snapshot, ZIP, SHA-256 |
| `manifest.py` | Run manifests |
| `audit.py` | JSONL audit log |
| `service.py` | `BackupService` — platform + all active tenants |
| `restore.py` | `RestoreService` — tenant / platform / full |

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/backup_all.py` | Run backup for one or all schedules |
| `scripts/backup_schedule.sh` | Cron entry point |
| `scripts/restore_tenant.py` | Restore single firm |
| `scripts/restore_platform.py` | Restore platform.db |
| `scripts/restore_full.py` | Full system restore |
| `run_backup.py` | Legacy entry → `BackupService` when `NEXAL_DATA_DIR` set |

## Ops API

`GET /api/ops/backup-health` — requires header `X-Nexal-Ops-Secret: $NEXAL_OPS_SECRET`

Returns last manifest, tenant count, restore readiness, recent audit.

## VPS cron

```
0 2 * * *  /opt/nexal-legal/scripts/backup_schedule.sh daily
0 3 * * 0  /opt/nexal-legal/scripts/backup_schedule.sh weekly
0 4 1 * *  /opt/nexal-legal/scripts/backup_schedule.sh monthly
```

## Restore examples

```bash
# List latest manifest
ls -t /var/lib/nexal-legal/backups/manifests/

# Restore one tenant
python3 scripts/restore_tenant.py \
  --firm-id <uuid> \
  --manifest /var/lib/nexal-legal/backups/manifests/daily_<run>.json \
  --yes

# Full restore (maintenance window)
python3 scripts/restore_full.py \
  --manifest /var/lib/nexal-legal/backups/manifests/weekly_<run>.json \
  --yes
```
