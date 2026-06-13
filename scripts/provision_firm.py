#!/usr/bin/env python3
"""CLI for provisioning Nexal Legal firm tenants (Phase 4A)."""
import argparse
import json
import sys

from nexal_platform.provision import provision_firm


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a new Nexal Legal firm tenant.")
    parser.add_argument("--firm-code", help="Human-readable firm code (e.g. FIRM001)")
    parser.add_argument("--name", required=True, help="Law firm display name")
    parser.add_argument("--slug", required=True, help="Unique firm slug (lowercase, hyphenated)")
    parser.add_argument("--owner-email", help="Primary owner email for platform_users")
    parser.add_argument("--portal-firm-id", help="Portal firms.id (Phase 4B bridge)")
    parser.add_argument("--portal-user-id", help="Portal customers.id (Phase 4B bridge)")
    args = parser.parse_args()

    try:
        result = provision_firm(
            name=args.name,
            slug=args.slug,
            firm_code=args.firm_code,
            owner_email=args.owner_email,
            portal_firm_id=args.portal_firm_id,
            portal_user_id=args.portal_user_id,
        )
    except Exception as exc:
        print(f"Provisioning failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
