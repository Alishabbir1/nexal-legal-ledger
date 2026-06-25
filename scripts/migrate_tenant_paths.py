#!/usr/bin/env python3
"""Remap workspace tenant database paths away from forbidden deploy directories."""
from __future__ import annotations

import argparse
import json
import sys

from _bootstrap import bootstrap_repo_root

bootstrap_repo_root()

from nexal_platform.config import get_platform_paths, is_forbidden_runtime_path
from nexal_platform.platform_db import PlatformDatabase


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fix workspace database_path values pointing at /root or the git clone."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist remapped paths (default: dry-run report only)",
    )
    args = parser.parse_args()

    platform = PlatformDatabase()
    paths = get_platform_paths()
    conn = platform.get_connection()
    rows = conn.execute("SELECT firm_id, database_path FROM workspaces ORDER BY firm_id").fetchall()
    conn.close()

    changes = []
    for row in rows:
        firm_id = row["firm_id"]
        stored = row["database_path"]
        if not is_forbidden_runtime_path(stored):
            continue
        canonical = paths.tenant_db_path(firm_id)
        changes.append({"firm_id": firm_id, "from": stored, "to": canonical})
        if args.apply:
            platform.update_workspace_database_path(firm_id, canonical)

    report = {
        "data_root": paths.root,
        "forbidden_paths_found": len(changes),
        "applied": args.apply,
        "changes": changes,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
