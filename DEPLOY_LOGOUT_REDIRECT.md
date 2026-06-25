# Ledger Logout Redirect — VPS Deployment

Production ledger at `https://ledger.nexallegal.co.uk` must run commit **including** `lib/portal_auth.py` logout fixes.

Until deployed, sign-out continues redirecting to `https://nexallegal.co.uk/portal` (IONOS parked page).

## Root cause

1. **VPS not updated** — fix is on GitHub `main` but the live service still runs pre-fix code.
2. **Wrong portal URL** — VPS likely has `NEXAL_PORTAL_URL=https://nexallegal.co.uk` (parked domain). New code replaces this automatically.

## Deploy on VPS (IONOS console or SSH)

```bash
ssh root@194.164.173.23
```

```bash
cd /root/nexal-legal-ledger
git fetch origin main
git checkout main
git pull origin main
git log -1 --oneline
```

Update environment (recommended — edit your systemd unit or `.env`):

```bash
grep -E 'NEXAL_PORTAL|PORTAL_APP|Environment' /etc/systemd/system/nexal-ledger.service
```

Set:

```bash
NEXAL_PORTAL_URL=https://nexal-legal.vercel.app
```

If using systemd `Environment=` lines, edit the unit then:

```bash
sudo systemctl daemon-reload
```

Run tests and restart:

```bash
python3 -m pytest tests/test_phase4e_sso_only.py tests/test_phase4e_portal_audit.py -q
sudo systemctl restart nexal-ledger
sudo systemctl status nexal-ledger --no-pager
```

Or use the deploy script:

```bash
bash scripts/deploy_logout_redirect.sh
```

## Verify production

```bash
curl -sI https://ledger.nexallegal.co.uk/logout | tr -d '\r' | grep -i location
curl -sI https://ledger.nexallegal.co.uk/auth/sso/logout | tr -d '\r' | grep -i location
```

**Expected:**

```
Location: https://nexal-legal.vercel.app/
```

**Must NOT contain:** `nexallegal.co.uk/portal`

## Portal (Vercel)

No Ledger deploy needed on Vercel. Portal sign-out already uses `/` on the Vercel host.

Ensure Vercel env: `NEXT_PUBLIC_APP_URL=https://nexal-legal.vercel.app`
