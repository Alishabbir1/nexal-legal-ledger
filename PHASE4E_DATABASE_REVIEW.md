# Phase 4E — Authentication Database Review

Ledger becomes SSO-only. Portal is the identity provider. This report documents
authentication-related database structures **before** any schema removal.

**Status:** Review complete — **no columns or tables deleted in Phase 4E**.
Obsolete structures are retained for rollback safety and historical audit data.

---

## Tenant `users` table (per-firm SQLite)

| Column | Purpose | Phase 4E status |
|--------|---------|-----------------|
| `user_id` | Primary key | **Keep** — SSO session mapping |
| `username` | Ledger login name | **Keep** — provisioned from Portal |
| `password_hash` | Stored hash (synced from Portal JWT) | **Keep** — SSO hash sync only; not used for direct Ledger login |
| `role` | Ledger role (admin/staff/…) | **Keep** — permissions |
| `active` | Account enabled | **Keep** — deactivate still used locally |
| `email` | Portal email | **Keep** — SSO lookup |
| `name` | Display name | **Keep** |
| `portal_user_id` | Portal JWT `sub` | **Keep** — SSO user mapping |
| `is_system` | Seeded system users | **Keep** |
| `login_attempts` | Failed direct-login counter | **Obsolete** — direct login removed; retain for audit |
| `login_lockout_until` | Direct-login lockout | **Obsolete** — retain |
| `admin_recovery_key_hash` | Admin recovery key | **Obsolete** — recovery removed |
| `admin_recovery_attempts` | Recovery attempt counter | **Obsolete** |
| `admin_recovery_last_attempt` | Last recovery attempt time | **Obsolete** |
| `admin_recovery_key_used` | One-time key flag | **Obsolete** |
| `admin_recovery_key_created_at` | Key creation time | **Obsolete** |
| `admin_recovery_confirm_attempts` | Password confirm for key regen | **Obsolete** |
| `admin_recovery_confirm_lockout_until` | Confirm lockout | **Obsolete** |
| `admin_recovery_confirm_lockout_level` | Escalating lockout | **Obsolete** |
| `temporary_password` | Force-change flag | **Obsolete** — Portal manages passwords |

### Recommended future migration (post-stabilisation)

1. Stop writing to obsolete columns (done in Phase 4E application layer).
2. After 90 days in production, drop recovery and direct-login columns in a
   dedicated migration script per tenant DB.
3. Keep `password_hash` — still updated via SSO JWT sync for legacy compatibility.

---

## `reset_tokens` table

| Column | Purpose | Phase 4E status |
|--------|---------|-----------------|
| `token` | Reset link token | **Obsolete** — Portal handles reset |
| `user_id` | Target user | **Obsolete** |
| `expires_at` | Token expiry | **Obsolete** |
| `used` | One-time use flag | **Obsolete** |

Table may be empty on SSO-only firms. Safe to drop in a future migration.

---

## Platform registry (`platform.db` / `nexal_platform`)

| Structure | Purpose | Phase 4E status |
|-----------|---------|-----------------|
| `firms` | Firm registry + `portal_firm_id` | **Keep** — tenant routing |
| `firm_databases` | Tenant DB paths | **Keep** |
| Session fields (`firm_id`, `sso_login`, `portal_user_id`) | Flask session | **Keep** — not DB |

---

## SSO mapping fields (must not remove)

- `firms.portal_firm_id` — links Portal firm to Ledger tenant
- `users.portal_user_id` — links Portal user to Ledger user
- `users.email` — identifier for provisioning
- `users.password_hash` — synced from Portal JWT (not for direct auth)

---

## Application routes removed (Phase 4E)

| Route | Replacement |
|-------|-------------|
| `GET/POST /login` | Redirect → Portal `/login` |
| `/admin/recovery` | Redirect → Portal `/login` |
| `/admin/recovery/reset` | Redirect → Portal `/login` |
| `/admin-reset-password/<token>` | Redirect → Portal `/login` |
| `/reset-password/<token>` | Redirect → Portal `/login` |
| `/force-password-change` | Redirect → Portal `/login` |
| Recovery key generation | Removed — Portal only |
| Ledger user invite with temp password | Disabled — Portal invitations |

---

## Invitation architecture (future)

Portal workflow (not implemented in Ledger):

```
Portal Admin → Invite User → Email → User sets password → Portal login → Launch → /auth/sso
```

Ledger receives provisioned users via SSO JWT only. No invitation emails or
password creation in Ledger.
