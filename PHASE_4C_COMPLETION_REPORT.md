# Nexal Legal — Phase 4C Completion Report

**Date:** 2026-06-11  
**Status:** Phase 4C automatable validation **COMPLETE**  
**Portal ↔ Ledger integration:** Operational (Phase 4B verified in production)

---

## Executive summary

Phase 4C audited the full Portal ↔ Ledger integration, implemented security and data-integrity hardening, added automated integration tests, and validated multi-tenant isolation, SSO flows, role boundaries, and platform registry integrity.

| Metric | Result |
|--------|--------|
| Ledger automated tests | **38 / 38 passed** |
| Portal automated tests | **3 / 3 passed** |
| Production SSO flow | Verified operational (pre-4C) |
| Phase 4C completion | **95%** (see manual checks below) |

---

## Section 1 — Codebase audit

### Portal (`c:\techstac`)

| Area | Finding | Action |
|------|---------|--------|
| Launch flow | No onboarding gate on `/api/portal/launch` | **Fixed** — `evaluateLaunchEligibility()` requires `ready_for_launch` + complete firm data |
| SSO token | `firm_name` included for ledger auto-provision | Already present; launch uses customer email for username |
| Session/auth | Solid JWT cookie model, role separation | No change required |
| `firm_users` table | Schema only, unused | Documented as future multi-user work |
| Test coverage | None before 4C | **Added** `lib/launch-eligibility.test.ts` |

### Ledger (`c:\solicitor-web-sandbox`)

| Area | Finding | Action |
|------|---------|--------|
| SSO session binding | `firm_id` trusted without DB verification | **Fixed** — `validate_sso_session_binding()` on each request |
| Open redirect | `/auth/sso?next=https://evil` | **Fixed** — `safe_redirect_target()` |
| Email hijack on SSO | Email match overwrote `portal_user_id` | **Fixed** — conflict raises error |
| SSO session leakage | Prior session state persisted | **Fixed** — `session.clear()` before SSO establish |
| `read_only` role | Not enforced | **Fixed** — `lib/permissions.py` + `can_edit_client_details()` |
| `portal_firm_id` duplicates | No uniqueness | **Fixed** — partial unique index |
| Default template users | `admin`/`staff` in every tenant | Documented — manual hardening recommended |
| JWT replay | No `jti` store | Documented — Phase 5 recommendation |
| Legacy vs tenant DB split | Password login uses legacy path | Documented — deprecate legacy login on VPS |

---

## Section 2 — Multi-tenant testing

**Tests:** `test_firm_a_and_b_create_isolated_clients_users_and_ledger_entries`, `test_direct_database_routing_cannot_cross_tenants`, `test_portal_launch_resolves_correct_tenant_each_time`

**Verified:**
- Firm A and Firm B clients, users, and DB paths are isolated
- Direct `get_db_for_firm()` routing cannot cross-read tenant data
- Repeated SSO launches resolve the correct platform firm each time

---

## Section 3 — SSO validation

**Tests:** 8 SSO-focused tests in `tests/test_phase4c_integration.py`

| Scenario | Result |
|----------|--------|
| Valid JWT → session → dashboard | PASS |
| Expired JWT | PASS (rejected) |
| Invalid / tampered JWT | PASS (rejected) |
| Missing firm (auto-provision) | PASS |
| Existing firm reuse | PASS |
| Open redirect blocked | PASS |

---

## Section 4 — User & role testing

**Tests:** `test_portal_role_permission_boundaries` (4 parametrized cases), `test_role_mapping_for_portal_roles`

| Portal role | Ledger role | Modify | Admin | Finance | Clients |
|-------------|-------------|--------|-------|---------|---------|
| firm_admin | admin | Yes | Yes | Yes | Yes |
| cashier | staff | Yes | No | Yes | Yes |
| staff | staff | Yes | No | Yes | Yes |
| read_only | staff | No | No | No | No |

---

## Section 5 — Ledger functional testing

**Test:** `test_client_creation_and_balance_posting`

**Verified:** Client creation and `ledger_transactions` posting in tenant DB.

**Manual only:** Office account, reconciliation UI, PDF/CSV exports, full audit trail UI.

---

## Section 6 — Session & security testing

**Verified:** Session binding, logout, safe redirect, email conflict guard, override state cleared on SSO.

**Manual only:** JWT replay, 15-minute browser timeout, production TLS/cookie review.

---

## Section 7 — Data integrity testing

**Module:** `nexal_platform/data_integrity.py`

**Verified:** No orphan workspaces, tenant files exist, duplicate `portal_firm_id` rejected.

---

## Section 8 — Production readiness

| Item | Recommendation |
|------|----------------|
| `FLASK_SECRET_KEY` | Set on VPS (currently hardcoded default) |
| Tenant backups | Extend scheduler to all workspace paths |
| Template default users | Remove/disable on tenant clone |
| Portal deploy | Redeploy after launch-gate commit |

---

## Section 9 — Test execution

### Ledger

```bash
python -m pytest tests/ -v
```

**38 / 38 passed** (7 Phase 4A + 9 Phase 4B + 22 Phase 4C)

### Portal

```bash
npm run test
```

**3 / 3 passed**

---

## Remaining manual validation

### 1. Production launch gate (after portal deploy)

1. Customer **not** at `ready_for_launch` → Launch returns **403**
2. Admin approves → Launch opens ledger dashboard

### 2. Cross-browser SSO smoke test

Chrome and Firefox: Portal login → Launch → dashboard.

### 3. Production tenant isolation

Firm A creates `PROD-A-TEST` client; Firm B must not see it.

### 4. Session timeout

16 minutes idle → login redirect with timeout message.

### 5. Ops hardening

Set `FLASK_SECRET_KEY`; remove template default users; confirm tenant backups.

---

## Sign-off

| Question | Answer |
|----------|--------|
| Phase 4C completion | **95%** |
| Sign off after manual checks 1–3 | **Yes** |
| Next phase | **Phase 5** — Billing, multi-user portal roles, JWT transport, tenant backups |
