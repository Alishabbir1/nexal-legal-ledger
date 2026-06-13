# Nexal Legal – Phase 4A Implementation Report

**Date:** 2026-06-13  
**Phase:** 4A – Multi-Tenant Foundation  
**Status:** COMPLETE

---

## Executive Summary

Phase 4A implements the complete multi-tenant foundation for the Nexal Legal
platform using the approved **One Firm = One Database** architecture.
All objectives have been met. No existing functionality has been removed or broken.

---

## Files Created

| File | Purpose |
|------|---------|
| `platform_db.py` | Platform-level database manager (firms + workspaces tables) |
| `provisioning.py` | `provision_firm()` – automated firm provisioning function |
| `db_router.py` | Database routing foundation (firm → correct DB) |
| `phase4a_migrate.py` | Migration script – adds role columns to users table |
| `phase4a_test.py` | Complete test suite (6 test classes, 25+ tests) |
| `PHASE4A_IMPLEMENTATION.md` | This report |

---

## Files Modified

| File | Change |
|------|--------|
| `database.py` | No code changes required. Schema migration handled separately via `phase4a_migrate.py` to preserve backwards compatibility. The `Database(db_path=...)` constructor already supports per-firm routing. |

---

## Database Changes

### New: `/data/platform.db`

Platform-level SQLite database. Created automatically on first import of `PlatformDB`.

#### Table: `firms`

| Column | Type | Notes |
|--------|------|-------|
| `firm_id` | TEXT PK | e.g. FIRM001 |
| `firm_name` | TEXT | Full legal name |
| `status` | TEXT | active / suspended / archived |
| `db_path` | TEXT | Absolute path to firm's solicitor_ledger.db |
| `created_at` | TEXT | ISO datetime |
| `updated_at` | TEXT | ISO datetime |

#### Table: `workspaces`

| Column | Type | Notes |
|--------|------|-------|
| `workspace_id` | TEXT PK | e.g. WS-FIRM001 |
| `firm_id` | TEXT FK | References firms(firm_id) |
| `workspace_name` | TEXT | Human-readable name |
| `db_path` | TEXT | Same as firm db_path (one workspace per firm in 4A) |
| `status` | TEXT | active / suspended / archived |
| `created_at` | TEXT | ISO datetime |
| `updated_at` | TEXT | ISO datetime |

---

### Modified: Per-firm `solicitor_ledger.db` – users table

The following columns are added by `phase4a_migrate.py`:

| Column | Type | Notes |
|--------|------|-------|
| `firm_id` | TEXT | Links user to a firm (NULL = legacy) |
| `email` | TEXT | User email – SSO readiness (Phase 4B) |
| `portal_user_id` | TEXT | Portal UUID – SSO bridge (Phase 4B) |

Existing columns and data are untouched.

---

## Tables Created

- `firms` (in platform.db)
- `workspaces` (in platform.db)

---

## Functions Created

### `platform_db.PlatformDB`
- `create_firm(firm_id, firm_name, db_path)`
- `get_firm(firm_id)`
- `list_firms(status=None)`
- `update_firm_status(firm_id, status)`
- `firm_exists(firm_id)`
- `create_workspace(workspace_id, firm_id, workspace_name, db_path)`
- `get_workspace(workspace_id)`
- `get_workspace_for_firm(firm_id)`
- `list_workspaces(firm_id=None)`
- `update_workspace_status(workspace_id, status)`

### `provisioning`
- `provision_firm(firm_id, firm_name)` — core provisioning entry-point
- `deprovision_firm(firm_id, delete_files=False)` — soft archive
- `ensure_template_db()` — idempotent template initialisation

### `db_router`
- `get_db_for_firm(firm_id)`
- `get_db_for_workspace(workspace_id)`
- `resolve_firm_db_path(firm_id)`
- `list_active_firm_ids()`
- `verify_isolation(firm_id_a, firm_id_b)`
- `clear_db_cache()`

### `phase4a_migrate`
- `migrate_db(db_path)` — migrates a single DB
- `run_migration(data_root=None)` — discovers and migrates all DBs

---

## Architecture

```
/data/
  platform.db                        ← firms + workspaces tables
  template/
    solicitor_ledger.db              ← clean schema template (never modified)
  firms/
    FIRM001/
      solicitor_ledger.db            ← Firm A's isolated DB
    FIRM002/
      solicitor_ledger.db            ← Firm B's isolated DB
    FIRM003/
      solicitor_ledger.db            ← Firm C's isolated DB
```

**Routing:**
```
Request (firm_id=FIRM001)
  → db_router.get_db_for_firm("FIRM001")
  → platform.db lookup → db_path = /data/firms/FIRM001/solicitor_ledger.db
  → Database(/data/firms/FIRM001/solicitor_ledger.db)
  → Fully isolated Firm A data
```

---

## Provisioning Workflow

```
provision_firm("FIRM001", "Smith & Partners LLP")
  1. Validate firm_id and firm_name
  2. Check firm does not already exist in platform.db
  3. ensure_template_db() → create /data/template/solicitor_ledger.db if missing
  4. Create /data/firms/FIRM001/ directory
  5. Clone template → /data/firms/FIRM001/solicitor_ledger.db
  6. INSERT INTO firms (FIRM001, "Smith & Partners LLP", ...)
  7. INSERT INTO workspaces (WS-FIRM001, FIRM001, ...)
  8. Return { firm_id, workspace_id, db_path, status, created_at }
```

---

## Workspace Workflow

Each firm gets one workspace in Phase 4A:
- Workspace ID is derived deterministically: `WS-{FIRM_ID}`
- Workspace and firm share the same db_path (one DB per firm)
- Multiple workspaces per firm is supported by the schema but not implemented until Phase 4C

---

## Role Foundation

### Supported Roles (Phase 4A)

| Role | Description | Phase |
|------|-------------|-------|
| `admin` | Full system access (existing) | Pre-4A |
| `staff` | General staff access (existing) | Pre-4A |
| `firm_admin` | Firm-level administrator | 4A (prepared) |
| `cashier` | Cashier / accounting access | 4A (prepared) |
| `read_only` | Read-only access | 4A (prepared) |

### New User Columns

- `firm_id` → links a user to a specific firm
- `email` → email address (SSO readiness)
- `portal_user_id` → portal UUID (future SSO bridge, Phase 4B)

---

## Test Results

### Test Classes

| Class | Tests | Coverage |
|-------|-------|---------|
| `TestPlatformDB` | 7 | firms table, workspaces table, CRUD operations |
| `TestProvisioning` | 7 | provision_firm, validation, multiple firms |
| `TestDBRouter` | 5 | get_db_for_firm, get_db_for_workspace, routing |
| `TestTenantIsolation` | 3 | Path isolation, data isolation, 3-firm isolation |
| `TestRoleFoundation` | 3 | Column migration, backwards compat, idempotency |
| `TestBackwardsCompatibility` | 3 | Legacy DB, existing roles, platform/ledger separation |
| **Total** | **28** | |

### Key Verifications

- [x] provisioning creates DB file for each firm
- [x] database creation confirmed (file exists after provision_firm)
- [x] workspace creation confirmed (workspace record in platform.db)
- [x] isolation confirmed (different file paths, no data crossover)
- [x] role support prepared (firm_admin, cashier, read_only)
- [x] existing ledger functionality preserved
- [x] existing admin/staff users unaffected
- [x] migration is idempotent (safe to run twice)
- [x] no production data deleted

---

## VPS Deployment Instructions

Run the following on the VPS after deploying Phase 4A code:

```bash
# 1. SSH to VPS
cd /opt/nexal-legal-ledger   # or wherever app is deployed

# 2. Run role foundation migration (adds columns to existing users table)
python phase4a_migrate.py

# 3. Provision test firms
python -c "
from provisioning import provision_firm
r1 = provision_firm('FIRM001', 'Alpha Law LLP')
r2 = provision_firm('FIRM002', 'Beta Solicitors Ltd')
r3 = provision_firm('FIRM003', 'Gamma Legal')
print('Provisioned:', r1['firm_id'], r2['firm_id'], r3['firm_id'])
"

# 4. Run test suite
python phase4a_test.py

# 5. Restart the ledger service
sudo systemctl restart nexal-legal-ledger
```

---

## Phase 4 Completion Percentage

| Sub-phase | Scope | Status | % |
|-----------|-------|--------|---|
| **Phase 4A** | Multi-tenant foundation | **COMPLETE** | **100%** |
| Phase 4B | SSO / Portal-to-Ledger identity mapping | Not started | 0% |
| Phase 4C | Multi-workspace per firm | Not started | 0% |
| Phase 4D | Advanced provisioning / billing readiness | Not started | 0% |
| **Phase 4 Overall** | | | **~25%** |

---

## Remaining Work for Phase 4B

1. **SSO Implementation** — JWT token flow from portal to ledger
2. **portal_user_id linking** — populate `portal_user_id` column on login
3. **Firm auto-detection on login** — read firm context from JWT
4. **Route middleware** — automatically route logged-in users to their firm DB
5. **Firm admin endpoints** — API routes for firm admin operations
6. **Portal ↔ Ledger identity bridge** — use `email` column for SSO matching

---

## Constraints Honoured

- No SSO implemented (Phase 4B)
- No billing / Stripe / subscriptions (Phase 5)
- No shared-database tenancy
- No tenant_id added to ledger tables
- No production data deleted
- Existing login flow unchanged
- Existing users working
- Existing functionality preserved
