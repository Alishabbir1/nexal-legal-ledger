# Nexal Legal — Phase 4A Multi-Tenant Foundation

## Overview

Phase 4A introduces a **one-database-per-firm** architecture for the Nexal Legal ledger application.

```
Firm A  →  data/tenants/{firm_a_id}/ledger.db
Firm B  →  data/tenants/{firm_b_id}/ledger.db
Firm C  →  data/tenants/{firm_c_id}/ledger.db
```

Platform metadata is stored separately in `data/platform.db`.

## Directory layout

```
data/
├── platform.db                 # firms, workspaces, platform_users
├── templates/
│   └── solicitor_ledger.db        # clean schema clone source
└── tenants/
    └── {firm_id}/
        └── solicitor_ledger.db    # one isolated DB per firm
```

Override the root with `NEXAL_DATA_DIR` (recommended on the VPS).

## Platform tables

### firms
- `id`, `name`, `slug`, `status`, `portal_firm_id`, timestamps

### workspaces
- `id`, `firm_id`, `database_path`, `status`, timestamps

### users
- `id`, `firm_id`, `email`, `portal_user_id`, `status`, timestamps

See `PHASE4A_IMPLEMENTATION.md` for full VPS deployment instructions.

## Provisioning

```bash
python scripts/provision_firm.py \
  --name "Henderson & Clarke LLP" \
  --slug henderson-clarke \
  --owner-email partner@hendersonclarke.example
```

## Routing

```python
from nexal_platform.router import TenantRouter

router = TenantRouter()
db = router.get_database(firm_id)
```

## Tenant isolation check

```bash
python -m pytest tests/test_phase4a_multitenant.py -v
```

## Phase 4B (not implemented here)

- JWT / SSO
- Portal login integration
- Launch Application handoff
- Billing / Stripe

## VPS deployment notes

1. Set `NEXAL_DATA_DIR` to a persistent path (e.g. `/var/lib/nexal-legal`).
2. Run provisioning for each onboarded firm.
3. Wire request handling to `TenantRouter` once portal authentication is integrated (Phase 4B).
