# VAT Module — VPS Deployment

Production Ledger at `https://ledger.nexallegal.co.uk`.

## Common failure: wrong deploy directory

The VPS may have **two** ledger clones:

| Path | Notes |
|------|--------|
| `/root/nexal-legal-ledger` | Often used for manual `git pull` |
| `/opt/nexal-ledger` | Often the **systemd WorkingDirectory** gunicorn actually runs |

If you `git pull` in `/root/nexal-legal-ledger` but `nexal-ledger.service` uses
`WorkingDirectory=/opt/nexal-ledger`, **live will keep serving old code** (no VAT column,
no setup page, no summary panel).

## One-command fix (IONOS console or SSH)

```bash
curl -fsSL https://raw.githubusercontent.com/Alishabbir1/nexal-legal-ledger/main/scripts/bootstrap_deploy_vat.sh | bash
```

Or if the repo is already cloned:

```bash
cd "$(systemctl show nexal-ledger -p WorkingDirectory --value)"
git pull origin main
bash scripts/deploy_vat_module.sh
```

## Manual diagnosis (exact order)

```bash
# 1. Commit in the directory gunicorn uses
cd "$(systemctl show nexal-ledger -p WorkingDirectory --value)"
git log -1 --oneline
# Expected: 83674ff feat: add VAT module...

# 2. Template marker on disk
grep -n "VAT-COLUMN-ENABLED" templates/office_import_review.html

# 3. systemd unit
sudo systemctl cat nexal-ledger | grep -E 'WorkingDirectory|ExecStart'

# 4. Stale bytecode
find . -name "*.pyc" -delete
find . -name "__pycache__" -type d

# 5. Processes
ps aux | grep gunicorn

# 6. Hard restart
sudo systemctl restart nexal-ledger
sudo systemctl status nexal-ledger --no-pager
```

## Verify in browser

After login:

1. **Office Account** — VAT summary panel (or “Activate VAT Module” link)
2. **`/office-account/vat/setup`** — quarter picker + period start date
3. **Upload statement** — VAT column between Source and Cleared

Portal (`nexallegal.co.uk`) — no changes required.
