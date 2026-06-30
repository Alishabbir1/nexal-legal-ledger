#!/usr/bin/env bash
# Configure production secrets on the Ledger VPS (ops, Flask session, SSO sync).
# Run on the VPS as root. Requires openssl and python3.
set -euo pipefail

ENV_FILE="${NEXAL_LEDGER_ENV_FILE:-/etc/nexal-ledger.env}"
SERVICE="${SERVICE:-nexal-ledger}"
SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
DROPIN_FILE="${DROPIN_DIR}/99-nexal-env.conf"
LEDGER_PORT="${LEDGER_PORT:-5001}"
APP_DIR="${APP_DIR:-/opt/nexal-ledger}"
DEV_FLASK_SECRET="sra-compliant-secret-key-change-in-production"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root on the Ledger VPS." >&2
  exit 1
fi

mkdir -p "$(dirname "${ENV_FILE}")"
touch "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

export ENV_FILE SERVICE_FILE DROPIN_DIR DROPIN_FILE DEV_FLASK_SECRET APP_DIR SERVICE LEDGER_PORT

python3 <<'PY'
from __future__ import annotations

import os
import re
import secrets
import shlex
import subprocess
import sys
from pathlib import Path

ENV_FILE = Path(os.environ["ENV_FILE"])
SERVICE_FILE = Path(os.environ["SERVICE_FILE"])
DROPIN_DIR = Path(os.environ["DROPIN_DIR"])
DROPIN_FILE = Path(os.environ["DROPIN_FILE"])
DEV_FLASK_SECRET = os.environ["DEV_FLASK_SECRET"]
APP_DIR = Path(os.environ["APP_DIR"])
SERVICE = os.environ["SERVICE"]
LEDGER_PORT = os.environ["LEDGER_PORT"]
MIN_SECRET_LEN = 16


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            values[key.strip()] = value
    return values


def write_env_file(values: dict[str, str]) -> None:
    lines: list[str] = []
    for key in sorted(values.keys()):
        lines.append(f"{key}={values[key]}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)


def is_valid_secret(value: str | None, *, min_len: int = MIN_SECRET_LEN) -> bool:
    return bool(value and len(value) >= min_len)


def is_usable_flask_secret(value: str | None) -> bool:
    return is_valid_secret(value) and value != DEV_FLASK_SECRET


def parse_systemd_environment(line: str) -> dict[str, str]:
    raw = line.split("=", 1)[1].strip()
    values: dict[str, str] = {}
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if value:
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_systemd_environment() -> dict[str, str]:
    merged: dict[str, str] = {}
    paths = [SERVICE_FILE]
    if SERVICE_FILE.parent.is_dir():
        paths.extend(sorted(SERVICE_FILE.parent.glob(f"{SERVICE.name}.service.d/*.conf")))
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("Environment="):
                merged.update(parse_systemd_environment(line))
    return merged


def scrub_dev_flask_from_unit(path: Path) -> bool:
    if not path.is_file():
        return False
    original = path.read_text(encoding="utf-8")
    updated = original
    for key in ("FLASK_SECRET_KEY", "SECRET_KEY"):
        updated = re.sub(
            rf'(\s|^){re.escape(key)}={re.escape(DEV_FLASK_SECRET)}(\s|$)',
            " ",
            updated,
        )
        updated = re.sub(rf'{re.escape(key)}={re.escape(DEV_FLASK_SECRET)}\s*', "", updated)
    updated = re.sub(r"Environment=\s+", "Environment=", updated)
    updated = re.sub(r"Environment=\s*$", "", updated, flags=re.MULTILINE)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def ensure_dropin_env_file() -> None:
    DROPIN_DIR.mkdir(parents=True, exist_ok=True)
    DROPIN_FILE.write_text(
        "[Service]\n"
        f"EnvironmentFile=-{ENV_FILE}\n",
        encoding="utf-8",
    )


def ensure_values() -> dict[str, str]:
    values = parse_env_file(ENV_FILE)
    values.setdefault("NEXAL_PRODUCTION", "true")
    systemd_env = read_systemd_environment()

    ops = values.get("NEXAL_OPS_SECRET") or systemd_env.get("NEXAL_OPS_SECRET") or os.environ.get("NEXAL_OPS_SECRET")
    if not is_valid_secret(ops):
        ops = secrets.token_hex(32)
        print("Generated new NEXAL_OPS_SECRET.")
    else:
        print("Using existing NEXAL_OPS_SECRET.")
    values["NEXAL_OPS_SECRET"] = ops

    flask = values.get("FLASK_SECRET_KEY")
    if not is_usable_flask_secret(flask):
        flask = systemd_env.get("FLASK_SECRET_KEY")
    if not is_usable_flask_secret(flask):
        flask = systemd_env.get("SECRET_KEY")
    if not is_usable_flask_secret(flask):
        flask = secrets.token_hex(32)
        print("Generated new FLASK_SECRET_KEY.")
    else:
        print("Using existing FLASK_SECRET_KEY.")
    values["FLASK_SECRET_KEY"] = flask

    sso = values.get("SSO_SECRET_KEY")
    if not is_valid_secret(sso):
        sso = systemd_env.get("SSO_SECRET_KEY") or systemd_env.get("NEXAL_SSO_SECRET")
    if not is_valid_secret(sso):
        print(
            f"ERROR: SSO_SECRET_KEY is missing from {ENV_FILE} and systemd.\n"
            f"Set SSO_SECRET_KEY in {ENV_FILE} to match Portal LEDGER_SSO_SECRET, then re-run.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("Using existing SSO_SECRET_KEY.")
    values["SSO_SECRET_KEY"] = sso

    write_env_file(values)

    for required in ("NEXAL_PRODUCTION", "NEXAL_OPS_SECRET", "FLASK_SECRET_KEY", "SSO_SECRET_KEY"):
        if required not in parse_env_file(ENV_FILE):
            print(f"FATAL: {required} missing from {ENV_FILE} after write.", file=sys.stderr)
            sys.exit(1)

    return values


def validate_production_secrets() -> None:
    if not APP_DIR.is_dir():
        print(f"Skipping Python validation (APP_DIR {APP_DIR} not found).")
        return
    env = os.environ.copy()
    env["NEXAL_LEDGER_ENV_FILE"] = str(ENV_FILE)
    env.setdefault("NEXAL_PRODUCTION", "true")
    env["PYTHONPATH"] = str(APP_DIR)
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "from nexal_platform.ops_secret import bootstrap_ledger_env, get_expected_ops_secret, get_flask_secret; "
                "from nexal_platform.production_secrets import validate_production_secrets; "
                "bootstrap_ledger_env(); "
                "validate_production_secrets("
                "sso_secret=os.environ.get('SSO_SECRET_KEY') or os.environ.get('NEXAL_SSO_SECRET'), "
                "flask_secret=get_flask_secret() or None, "
                "ops_secret=get_expected_ops_secret() or None); "
                "print('Production secret validation passed.')"
            ),
        ],
        check=True,
        env=env,
        cwd=str(APP_DIR),
    )


values = ensure_values()

if SERVICE_FILE.is_file() and scrub_dev_flask_from_unit(SERVICE_FILE):
    print(f"Removed dev FLASK/SECRET defaults from {SERVICE_FILE}.")

ensure_dropin_env_file()
print(f"Wrote systemd drop-in {DROPIN_FILE} (EnvironmentFile loads last).")

validate_production_secrets()

subprocess.run(["systemctl", "daemon-reload"], check=True)
subprocess.run(["systemctl", "restart", SERVICE], check=True)
subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], check=True)

ops_secret = values["NEXAL_OPS_SECRET"]
root = subprocess.run(
    ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "10", f"http://127.0.0.1:{LEDGER_PORT}/"],
    capture_output=True,
    text=True,
)
health = subprocess.run(
    [
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--max-time",
        "10",
        "-H",
        f"X-Nexal-Ops-Secret: {ops_secret}",
        f"http://127.0.0.1:{LEDGER_PORT}/api/ops/backup-health",
    ],
    capture_output=True,
    text=True,
)

print("")
print("Ledger service restarted.")
print(f"Local root HTTP status: {root.stdout.strip() or 'unavailable'}")
print(f"Local backup-health HTTP status: {health.stdout.strip() or 'unavailable'}")
print("")
print("Set the SAME value on Vercel (Portal production):")
print(f"  NEXAL_OPS_SECRET={ops_secret}")
print("")

if health.stdout.strip() != "200":
    print(
        f"WARNING: Local backup-health did not return HTTP 200. Check: journalctl -u {SERVICE} -n 50 --no-pager",
        file=sys.stderr,
    )
    sys.exit(1)
PY
