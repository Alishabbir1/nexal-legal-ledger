# Nexal Legal — Phase 4B Portal ↔ Ledger Integration

## Overview

Phase 4B connects the Nexal Legal portal to the ledger using short-lived JWT SSO.

**Target flow:**

Portal Login → Portal Dashboard → Launch Ledger → Auto-login → Firm database loaded

## Environment variables

### Portal (Vercel)

| Variable | Purpose |
|---|---|
| `LEDGER_APP_URL` | Ledger base URL (`https://ledger.nexallegal.co.uk`) |
| `LEDGER_SSO_SECRET` | Shared HS256 secret (must match ledger) |
| `LEDGER_SSO_TTL` | Token lifetime seconds (default 300) |

### Ledger (VPS)

| Variable | Purpose |
|---|---|
| `SSO_SECRET_KEY` or `NEXAL_SSO_SECRET` | Shared HS256 secret |
| `SSO_TOKEN_TTL` | Token lifetime seconds (default 300) |
| `NEXAL_DATA_DIR` | Phase 4A data root |

## JWT claims

| Claim | Source |
|---|---|
| `sub` | Portal `customers.id` |
| `email` | Portal user email |
| `firm_id` | Portal `firms.id` (mapped via `platform.db.portal_firm_id`) |
| `role` | `firm_admin`, `cashier`, `staff`, `read_only` |
| `iss` | `nexal-portal` |
| `aud` | `nexal-ledger` |

## Ledger endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/sso-login` | POST | JSON SSO login |
| `/auth/sso` | GET/POST | Browser redirect SSO entry |
| `/auth/sso/status` | GET | Session status JSON |
| `/auth/sso/logout` | GET/POST | Clear SSO session |

Legacy username/password login at `/login` remains available.

## Portal endpoint

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/portal/launch` | GET | Generate JWT and redirect to ledger |

## Deployment

### 1. Ledger VPS

```bash
cd /opt/nexal-legal-ledger
git pull origin main
export NEXAL_DATA_DIR=/var/lib/nexal-legal
export SSO_SECRET_KEY='<shared-secret>'
sudo systemctl restart nexal-ledger
```

### 2. Portal Vercel

Set environment variables:

- `LEDGER_APP_URL=https://ledger.nexallegal.co.uk`
- `LEDGER_SSO_SECRET=<same shared secret>`
- Redeploy portal

### 3. Provision firms with portal linkage

When provisioning ledger firms, pass portal firm id:

```bash
python3 scripts/provision_firm.py \
  --firm-code FIRM001 \
  --name "Alpha Law LLP" \
  --slug alpha-law-llp \
  --portal-firm-id <portal-firms.id>
```

## Validation

```bash
python3 -m pytest tests/test_phase4a_multitenant.py tests/test_phase4b_sso.py -v
python3 phase4a_test.py
```

## Security

- JWT signature verification (HS256)
- Expiry enforcement
- Issuer/audience validation
- Inactive firm/workspace rejection
- Per-firm database routing for SSO sessions
- SSO audit logging in ledger `audit_log`
