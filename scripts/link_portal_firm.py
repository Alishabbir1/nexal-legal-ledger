#!/usr/bin/env python3
"""Provision a portal firm on the ledger VPS (Phase 4B manual fallback)."""
import argparse
import json
import sys

from nexal_platform.portal_link import resolve_active_portal_firm, slug_from_portal_firm
from nexal_platform.provision import provision_firm


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Link a portal firms.id to a ledger tenant (provision if missing)."
    )
    parser.add_argument("--portal-firm-id", required=True, help="Portal firms.id UUID")
    parser.add_argument("--name", required=True, help="Law firm display name")
    parser.add_argument("--owner-email", required=True, help="Portal customer email")
    parser.add_argument("--portal-user-id", help="Portal customers.id UUID")
    parser.add_argument("--firm-code", help="Optional ledger firm code")
    parser.add_argument("--slug", help="Optional slug (auto-derived if omitted)")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only check whether portal_firm_id resolves; do not provision",
    )
    args = parser.parse_args()

    slug = args.slug or slug_from_portal_firm(args.name, args.portal_firm_id)

    if args.verify_only:
        try:
            firm = resolve_active_portal_firm(args.portal_firm_id)
        except ValueError as exc:
            print(f"Not linked: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"linked": True, "firm": firm}, indent=2, default=str))
        return 0

    try:
        result = provision_firm(
            name=args.name,
            slug=slug,
            firm_code=args.firm_code,
            owner_email=args.owner_email,
            portal_firm_id=args.portal_firm_id,
            portal_user_id=args.portal_user_id,
        )
    except ValueError as exc:
        try:
            firm = resolve_active_portal_firm(args.portal_firm_id)
            print(json.dumps({"already_linked": True, "firm": firm}, indent=2, default=str))
            return 0
        except ValueError:
            print(f"Provisioning failed: {exc}", file=sys.stderr)
            return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
