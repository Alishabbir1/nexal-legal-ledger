# Phase 4E — Portal Link Audit & Final Verification

**Date:** 2026-06-08  
**Ledger commit:** (see git log after push)  
**Portal reference:** `c:\techstac` (Next.js app)

---

## 1. Portal Link Inventory

All user-facing Portal URLs are built by `lib/portal_auth.py` from **`NEXAL_PORTAL_URL`**
(with fallback `PORTAL_APP_URL`, then production default `https://nexallegal.co.uk`).

| UI / behaviour | Helper | Resolved URL | Portal route | Status |
|----------------|--------|--------------|--------------|--------|
| **Open Portal** (Account page) | `get_portal_dashboard_url()` | `{NEXAL_PORTAL_URL}/portal` | `/portal` | ✅ Live |
| **Manage Users in Portal** | `get_portal_users_url()` | `{NEXAL_PORTAL_URL}/portal/users` | `/portal/users` | ✅ Live |
| **Logout** / SSO logout | `portal_logout_redirect()` | `{NEXAL_PORTAL_URL}/portal` | `/portal` | ✅ Live |
| Unauthenticated access | `portal_login_redirect()` | `{NEXAL_PORTAL_URL}/login?…` | `/login` | ✅ Live |
| Legacy `/login` | `portal_login_redirect()` | `{NEXAL_PORTAL_URL}/login?…` | `/login` | ✅ Redirect only |
| Legacy recovery/reset routes | `portal_login_redirect()` | `{NEXAL_PORTAL_URL}/login?reason=legacy_route` | `/login` | ✅ Redirect only |

### Removed / dead (Ledger)

| Former link | Status |
|-------------|--------|
| Forgot Password (Portal) button | **Removed** — no Ledger UI |
| Admin Recovery | **Removed** — templates deleted |
| Ledger login page | **Removed** — template deleted |
| Reset password pages | **Removed** — templates deleted |
| Hardcoded `portal_url` in templates | **Removed** |

### Not used in Ledger UI

| Portal route | Notes |
|--------------|-------|
| `/forgot-password` | Portal-only; Ledger never links here |
| `/reset-password` | Portal-only |
| `/api/portal/launch` | Portal → Ledger SSO (inbound to Ledger `/auth/sso`) |

**No localhost or stale environment URLs** found in Ledger templates after audit.

---

## 2. Single source of truth

| Variable | Purpose |
|----------|---------|
| `NEXAL_PORTAL_URL` | **Primary** — set on Ledger VPS |
| `PORTAL_APP_URL` | Alias (matches Portal `NEXT_PUBLIC_APP_URL`) |
| Default | `https://nexallegal.co.uk` if unset |

**Production VPS:**

```bash
export NEXAL_PORTAL_URL=https://nexallegal.co.uk
```

---

## 3. Package badge verification

| Source | Mechanism |
|--------|-----------|
| SSO firms | `platform.db` → `firms.subscription_tier` via `resolve_firm_tier()` |
| Tenant cache | `system_config.firm_subscription_tier` |
| Header badge | `firm_package_label` from `package_display_label(tier)` |
| User Management | `package_usage_summary()` |

| Tier | Badge | Max users |
|------|-------|-----------|
| essential | Essential (£39/month) | 2 |
| professional | Professional (£79/month) | 5 |
| practice_plus | Practice Plus (£149/month) | 10 |

Definitions: `lib/subscription_packages.py` — **not hardcoded in templates**.

**Fix applied:** Context-processor error fallback now uses `package_display_label(DEFAULT_TIER)` instead of literal `'Essential (£39/month)'`.

---

## 4. User limit enforcement

| Check | Implementation | Status |
|-------|----------------|--------|
| SSO new user provision | `check_user_limit()` in `provision_portal_user()` | ✅ Blocks at limit |
| Ledger add-user POST | Flash + redirect (Portal-only) | ✅ No local creation |
| Deactivated users | Excluded: `WHERE active = 1` in counts | ✅ |
| System accounts | Excluded: `COALESCE(is_system, 0) = 0` | ✅ |
| Template seed admin/staff | Removed from provisioned tenants; `is_system=1` in legacy DB | ✅ |
| Reports "Created By" | `get_billable_active_users()` — excludes system | ✅ |

User activation at limit is enforced on **Portal → SSO provision** path (new/reactivated users entering via launch).

---

## 5. System accounts (`admin` / `staff`)

| Question | Answer |
|----------|--------|
| Why they exist | Legacy dev/template seed accounts in single-DB and template DB |
| System flag | `users.is_system = 1` for `admin` and `staff` |
| Provisioned tenants | Template copy **deletes** `admin`/`staff` rows |
| Package limits | **Excluded** from billable counts |
| User Management list | **Excluded** via `get_billable_users_for_management()` |
| Reports filter | **Excluded** via `get_billable_active_users()` |
| Customer-facing reports | **Do not appear** in Created By dropdowns |

---

## 6. Final health check

| Flow | Expected | Verified by tests |
|------|----------|-------------------|
| Portal → Launch → Ledger | SSO session, tenant DB | `test_phase4e_sso_only`, `test_phase4b_sso`, `test_phase4c_integration` |
| Logout → Portal dashboard | `/portal` | `test_scenario3_*`, `test_logout_redirects_*` |
| Account page → Open Portal | `/portal` | `test_open_portal_button_*` |
| Users page → Manage Users | `/portal/users` | `test_manage_users_button_*` |
| No Ledger login UI | Redirect only | Template scan + scenario tests |
| No recovery/reset UI | Redirect only | Template scan + scenario tests |

---

## 7. Dead links found

**None** in current Ledger UI. Legacy routes redirect safely to Portal login.

---

## 8. Intentionally retained (non-UI)

- `lib/portal_auth.py` — URL builders
- `lib/password_verification.py`, `lib/portal_password_sync.py` — SSO hash sync
- `database.py` recovery/password columns — documented for future migration
- Legacy redirect routes — bookmark safety
