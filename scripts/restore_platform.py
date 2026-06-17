#!/usr/bin/env python3
"""Restore platform.db from a backup manifest."""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore Nexal Legal platform.db")
    parser.add_argument("--manifest", required=True, help="Path to backup manifest JSON")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    from nexal_platform.backup import RestoreService

    service = RestoreService()
    try:
        result = service.restore_platform(args.manifest, assume_yes=args.yes)
    except Exception as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1

    print(f"Platform restored: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
