#!/usr/bin/env python3
"""diagnose_and_fix.py - Production diagnostic and path repair."""
import os, sqlite3, argparse

PLATFORM_DB = "/var/lib/nexal-legal/platform.db"
PROD_ROOT = "/var/lib/nexal-legal"

def is_forbidden(p):
      if not p: return True
            n = os.path.normpath(str(p)).lower()
    return n.startswith("/root") or ("nexal-legal-ledger" in n and not n.startswith(PROD_ROOT.lower()))

def canonical(firm_id):
      return os.path.join(PROD_ROOT, "tenants", firm_id, "solicitor_ledger.db")

def main():
      pa = argparse.ArgumentParser()
    pa.add_argument("--fix", action="store_true")
    args = pa.parse_args()

    # Env check
    ndd = os.environ.get("NEXAL_DATA_DIR", "")
    print(f"NEXAL_DATA_DIR='{ndd}'  forbidden={is_forbidden(ndd) if ndd else 'n/a'}")

    # Systemd env
    for f in ["/etc/systemd/system/nexal-ledger.service",
                            "/etc/systemd/system/nexal-ledger.service.d/override.conf"]:
                                      if os.path.isfile(f):
                                                    for line in open(f):
                                                                      if "NEXAL" in line or "Environ" in line:
                                                                                            print(f"SYSTEMD [{f}]: {line.rstrip()}")

                                                          # DB query
                                                          if not os.path.isfile(PLATFORM_DB):
                                                                    print(f"ERROR: {PLATFORM_DB} not found"); return
                                                                conn = sqlite3.connect(PLATFORM_DB)
                                            conn.row_factory = sqlite3.Row
    rows = conn.execute(
              "SELECT f.id,f.name,f.portal_firm_id,w.database_path,w.status "
              "FROM firms f LEFT JOIN workspaces w ON w.firm_id=f.id"
    ).fetchall()
    issues = []
    for r in rows:
              d = dict(r)
        bad = is_forbidden(d.get("database_path"))
        marker = " <<FORBIDDEN>>" if bad else " OK"
        print(f"Firm: {d['name'][:35]:35s} path={d['database_path']}{marker}")
        if bad: issues.append(d)
              print(f"\nTotal: {len(rows)} firms, {len(issues)} with forbidden paths")

    if args.fix:
              for d in issues:
                            np = canonical(d['id'])
            os.makedirs(os.path.dirname(np), exist_ok=True)
            conn.execute("UPDATE workspaces SET database_path=? WHERE firm_id=?", (np, d['id']))
            print(f"FIXED: {d['name'][:30]} -> {np}")
        conn.commit()
        print("Done. Restart nexal-ledger service.")
    conn.close()

if __name__ == "__main__":
      main()
