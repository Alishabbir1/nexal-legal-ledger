# VAT Module — VPS Deployment

Production Ledger at `https://ledger.nexallegal.co.uk`.

## Common failure: wrong deploy directory

The VPS may have **two** ledger clones, or a **missing** WorkingDirectory:

| Path | Notes |
|------|--------|
| `/root/nexal-legal-ledger` | Git clone — **use this as WorkingDirectory** |
| `/opt/nexal-ledger` | Sometimes configured in older systemd units |
| `/opt/nexal-legal` | **Broken** — path often does not exist; service fails silently or serves stale code |

If `WorkingDirectory` points at a path that does not exist or is not updated, live will not
show VAT (no column, no setup page, no summary panel) even after `git pull` elsewhere.

## Fix WorkingDirectory (confirmed production issue)

```bash
curl -fsSL https://raw.githubusercontent.com/Alishabbir1/nexal-legal-ledger/main/scripts/fix_ledger_working_directory.sh | bash
```

Manual equivalent:

```bash
sudo mkdir -p /etc/systemd/system/nexal-ledger.service.d
sudo tee /etc/systemd/system/nexal-ledger.service.d/working-directory.conf <<'EOF'
[Service]
WorkingDirectory=/root/nexal-legal-ledger
EOF
sudo systemctl daemon-reload
sudo systemctl restart nexal-ledger
sudo systemctl status nexal-ledger --no-pager
```

## One-command deploy (after WorkingDirectory is fixed)

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
