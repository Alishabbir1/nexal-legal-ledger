# Nexal Legal — Phase 4A Implementation & VPS Deployment

## Overview

Phase 4A delivers a **one-database-per-firm** multi-tenant foundation for the Nexal Legal ledger application.

| Component | Location |
|---|---|
| Platform database | `{NEXAL_DATA_DIR}/platform.db` |
| Template database | `{NEXAL_DATA_DIR}/templates/solicitor_ledger.db` |
| Firm databases | `{NEXAL_DATA_DIR}/tenants/{firm_id}/solicitor_ledger.db` |

Set `NEXAL_DATA_DIR` on the VPS (recommended: `/var/lib/nexal-legal`).

## Phase 4A files

| File | Purpose |
|---|---|
| `nexal_platform/` | Canonical implementation package |
| `platform_db.py` | Platform registry module (shim) |
| `provisioning.py` | Firm provisioning module (shim) |
| `db_router.py` | Database routing module (shim) |
| `phase4a_migrate.py` | Legacy single-tenant migration |
| `phase4a_test.py` | Full validation suite |
| `scripts/provision_firm.py` | CLI firm provisioning |
| `scripts/deploy_phase4a.sh` | VPS deployment script |
| `tests/test_phase4a_multitenant.py` | Pytest suite |

## Platform schema

### firms
`id`, `firm_code`, `name`, `slug`, `status`, `portal_firm_id`, timestamps

### workspaces
`id`, `firm_id`, `database_path`, `status`, timestamps

### users
`id`, `firm_id`, `email`, `portal_user_id`, `status`, timestamps

## VPS deployment (repeatable)

### 1. Pull latest code

```bash
cd /opt/nexal-legal-ledger   # or your deploy path
git pull origin main
```

### 2. Set data directory

```bash
export NEXAL_DATA_DIR=/var/lib/nexal-legal
sudo mkdir -p "$NEXAL_DATA_DIR"
sudo chown "$USER":"$USER" "$NEXAL_DATA_DIR"
```

Add to systemd service or `.env`:

```
NEXAL_DATA_DIR=/var/lib/nexal-legal
```

### 3. Run deployment script

```bash
bash scripts/deploy_phase4a.sh
```

This will:
1. Ensure template `solicitor_ledger.db` exists
2. Migrate legacy database (non-destructive copy) if present
3. Run Phase 4A validation tests
4. Print service restart reminder

### 4. Provision firms

```bash
python scripts/provision_firm.py \
  --firm-code FIRM001 \
  --name "Alpha Law LLP" \
  --slug alpha-law-llp \
  --owner-email admin@alpha-law.example
```

### 5. Restart ledger service

```bash
sudo systemctl restart nexal-ledger
# or your process manager equivalent
```

## Migration (legacy → multi-tenant)

Non-destructive (copies database):

```bash
python phase4a_migrate.py \
  --legacy-path /path/to/solicitor_ledger.db \
  --firm-code FIRM000 \
  --firm-name "Legacy Firm" \
  --slug legacy
```

The original file is preserved unless `--move` is passed.

## Validation

```bash
python phase4a_test.py
python -m pytest tests/test_phase4a_multitenant.py -v
```

Test firms validated:
- **FIRM001** — Alpha Law LLP
- **FIRM002** — Beta Solicitors Ltd
- **FIRM003** — Gamma Legal

## Backwards compatibility

- `app.py` continues to use the legacy `Database()` singleton unchanged
- Existing `solicitor_ledger.db` in the project root is not modified by default
- Phase 4B will wire request routing to `TenantRouter`

## Phase 4B (not in scope)

- JWT / SSO
- Portal login integration
- Launch Application handoff
- Billing / Stripe
