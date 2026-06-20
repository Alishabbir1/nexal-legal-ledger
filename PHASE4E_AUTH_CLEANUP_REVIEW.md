# Phase 4E — Authentication Cleanup Review

Ledger is SSO-only. All identity, credential, and account lifecycle operations belong to the
Portal. This document lists authentication-related code and database structures that remain
in the Ledger codebase **intentionally** vs what can be removed in a future migration.

**Status:** UI and routes cleaned (Phase 4E cleanup). **No database columns deleted.**

See also: [PHASE4E_DATABASE_REVIEW.md](PHASE4E_DATABASE_REVIEW.md)

---

## Removed from Ledger (application layer)

| Item | Action |
|------|--------|
| `/login` UI | Deleted `templates/login.html`; route redirects to Portal |
| Admin recovery UI | Deleted `templates/admin_recovery.html`, `admin_recovery_reset.html` |
| Password reset UI | Deleted `templates/reset_password.html`, `reset_link_generated.html` |
| Force password change UI | Deleted `templates/force_password_change.html` |
| Security page auth controls | Replaced with Portal-only informational message |
| User Management password UI | Removed reset/add-user forms; read-only user list |
| Forgot password button | Removed from Ledger entirely |
| Recovery key generation UI | Removed |
| Nav label "Security" | Renamed to "Account" |

Legacy URLs (`/admin/recovery`, `/reset-password/*`, etc.) **302 redirect to Portal login**
so bookmarks never render Ledger auth pages.

---

## Obsolete database columns (do not delete yet)

### `users` table (per-tenant SQLite)

| Column | Former purpose | Migration note |
|--------|----------------|----------------|
| `login_attempts` | Direct login lockout | Stop writing; drop after 90 days |
| `login_lockout_until` | Direct login lockout | Stop writing; drop after 90 days |
| `admin_recovery_key_hash` | Admin recovery key | Stop writing; drop after 90 days |
| `admin_recovery_attempts` | Recovery attempts | Stop writing; drop after 90 days |
| `admin_recovery_last_attempt` | Recovery timestamp | Stop writing; drop after 90 days |
| `admin_recovery_key_used` | One-time key flag | Stop writing; drop after 90 days |
| `admin_recovery_key_created_at` | Key creation time | Stop writing; drop after 90 days |
| `admin_recovery_confirm_attempts` | Key regen confirm | Stop writing; drop after 90 days |
| `admin_recovery_confirm_lockout_until` | Confirm lockout | Stop writing; drop after 90 days |
| `admin_recovery_confirm_lockout_level` | Escalating lockout | Stop writing; drop after 90 days |
| `temporary_password` | Force password change | Stop writing; drop after 90 days |

### `reset_tokens` table

Entire table obsolete — Portal owns reset workflow. Safe to drop in future migration when
confirmed empty across all tenant DBs.

---

## Retained intentionally (required for SSO)

| Item | Reason |
|------|--------|
| `users.password_hash` | Synced from Portal JWT; not used for Ledger login |
| `users.portal_user_id` | Maps Portal JWT `sub` to Ledger user |
| `users.email` | SSO provisioning identifier |
| `firms.portal_firm_id` | Multi-tenant routing |
| `/auth/sso`, `/api/sso-login` | SSO entry points |
| `lib/portal_auth.py` | Portal redirect URLs |
| `lib/password_verification.py` | SSO hash sync verification |
| `lib/portal_password_sync.py` | JWT password hash sync |
| `portal_bridge.py` | SSO session establishment |
| `lib/tenant_auth.py` | Internal tenant user lookup (no UI) |
| `database.py` recovery/password methods | Legacy DB API; unused by UI; remove with column migration |

---

## Portal ownership (authoritative)

- Login / Logout
- Forgot password / Reset password / Change password
- User invitations and activation
- User deactivation (Portal)
- Subscription and package limits (Portal enforces; Ledger displays usage)

---

## Recommended future migration (post-stabilisation)

1. Confirm zero writes to obsolete columns (audit logs / 90-day window).
2. Ship `scripts/migrate_drop_obsolete_auth_columns.py` per tenant DB.
3. Drop `reset_tokens` table.
4. Remove dead Python methods from `database.py` and `lib/tenant_auth.py`.
5. Remove legacy redirect routes if no longer needed.

---

## Verification checklist

- [x] Security page shows Portal-only message + "Open Portal"
- [x] User Management shows Portal message + "Manage Users in Portal"
- [x] No recovery/password templates in `templates/`
- [x] Logout → Portal dashboard
- [x] Unauthenticated → Portal login
- [x] SSO-only session enforcement (`sso_login` required)
