# VAT Module — VPS Deployment

Production Ledger: `https://ledger.nexallegal.co.uk`

## How bank statement upload was deployed (replicate this for VAT)

From `PHASE4B_IMPLEMENTATION.md` and `configure_production_env.py`:

| Setting | Value |
|---------|--------|
| **Code directory** | `/opt/nexal-legal-ledger` (or `/opt/nexal-ledger`) |
| **Data directory** | `/var/lib/nexal-legal` (`NEXAL_DATA_DIR`) |
| **Service** | `nexal-ledger` |
| **Process user** | `sync` (gunicorn — **cannot read `/root/`**) |

**Exact VPS commands used for every successful Ledger deploy:**

```bash
cd /opt/nexal-legal-ledger
git pull origin main
export NEXAL_DATA_DIR=/var/lib/nexal-legal
sudo systemctl restart nexal-ledger
sudo systemctl status nexal-ledger --no-pager
```

Nothing special in the service file except `WorkingDirectory` must point at the
**`/opt/...` git clone**, not `/root/nexal-legal-ledger` and not `/opt/nexal-legal`.

## Current production failure (Aug 2026)

- `WorkingDirectory=/opt/nexal-legal` — **path does not exist** (typo)
- Code was pulled into `/root/nexal-legal-ledger` — **sync cannot access**
- Service crashed: `Permission denied`

## One-command fix (urgent — service down)

```bash
curl -fsSL https://raw.githubusercontent.com/Alishabbir1/nexal-legal-ledger/main/scripts/deploy_ledger_production.sh | bash
```

This script:
1. Sets `WorkingDirectory=/opt/nexal-legal-ledger` via systemd drop-in
2. Clones repo to `/opt/nexal-legal-ledger` if missing
3. `git pull origin main` (includes VAT module)
4. Clears `.pyc` cache
5. Migrates tenant DBs (additive VAT columns/tables)
6. Repairs `/var/lib/nexal-legal` ownership for `sync`
7. Restarts `nexal-ledger`

## Manual fix (if preferred)

```bash
sudo mkdir -p /etc/systemd/system/nexal-ledger.service.d
sudo tee /etc/systemd/system/nexal-ledger.service.d/working-directory.conf <<'EOF'
[Service]
WorkingDirectory=/opt/nexal-legal-ledger
EOF

# Ensure repo exists at /opt (NOT /root)
if [[ ! -d /opt/nexal-legal-ledger/.git ]]; then
  sudo git clone https://github.com/Alishabbir1/nexal-legal-ledger.git /opt/nexal-legal-ledger
fi

cd /opt/nexal-legal-ledger
sudo git pull origin main
sudo chown -R root:sync /opt/nexal-legal-ledger
sudo chmod -R u=rwX,g=rX,o=rX /opt/nexal-legal-ledger

find /opt/nexal-legal-ledger -name "*.pyc" -delete
sudo systemctl daemon-reload
sudo systemctl restart nexal-ledger
sudo systemctl status nexal-ledger --no-pager
```

## Verify on live

1. https://ledger.nexallegal.co.uk loads (not 502)
2. Office Account → VAT summary or “Activate VAT Module”
3. `/office-account/vat/setup` → quarter picker + period start date
4. Upload statement → **VAT (20%)** column between Source and Cleared

Portal — no changes.
