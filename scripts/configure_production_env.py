#!/usr/bin/env python3
"""Configure Ledger production secrets on the VPS."""
from __future__ import annotations

import argparse
import os
import re
import secrets
import shlex
import subprocess
import sys
from pathlib import Path

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


def write_env_file(env_file: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={values[key]}" for key in sorted(values.keys())]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(env_file, 0o600)


def is_valid_secret(value: str | None, *, min_len: int = MIN_SECRET_LEN) -> bool:
    return bool(value and len(value) >= min_len)


def is_usable_flask_secret(value: str | None, dev_flask_secret: str) -> bool:
    return is_valid_secret(value) and value != dev_flask_secret


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


def read_systemd_environment(service_file: Path, service_name: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    paths = [service_file]
    dropin_dir = service_file.parent / f"{service_name}.service.d"
    if dropin_dir.is_dir():
        paths.extend(sorted(dropin_dir.glob("*.conf")))
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("Environment="):
                merged.update(parse_systemd_environment(line))
    return merged


def scrub_dev_flask_from_unit(path: Path, dev_flask_secret: str) -> bool:
    if not path.is_file():
        return False
    original = path.read_text(encoding="utf-8")
    updated = original
    for key in ("FLASK_SECRET_KEY", "SECRET_KEY"):
        updated = re.sub(
            rf"(\s|^){re.escape(key)}={re.escape(dev_flask_secret)}(\s|$)",
            " ",
            updated,
        )
        updated = re.sub(rf"{re.escape(key)}={re.escape(dev_flask_secret)}\s*", "", updated)
    updated = re.sub(r"Environment=\s+", "Environment=", updated)
    updated = re.sub(r"Environment=\s*$", "", updated, flags=re.MULTILINE)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def ensure_dropin_env_file(dropin_dir: Path, dropin_file: Path, env_file: Path) -> None:
    dropin_dir.mkdir(parents=True, exist_ok=True)
    dropin_file.write_text(
        "[Service]\n"
        f"EnvironmentFile=-{env_file}\n",
        encoding="utf-8",
    )


def ensure_values(
    env_file: Path,
    service_file: Path,
    service_name: str,
    dev_flask_secret: str,
) -> dict[str, str]:
    values = parse_env_file(env_file)
    values.setdefault("NEXAL_PRODUCTION", "true")
    systemd_env = read_systemd_environment(service_file, service_name)

    ops = (
        values.get("NEXAL_OPS_SECRET")
        or systemd_env.get("NEXAL_OPS_SECRET")
        or os.environ.get("NEXAL_OPS_SECRET")
    )
    if not is_valid_secret(ops):
        ops = secrets.token_hex(32)
        print("Generated new NEXAL_OPS_SECRET.")
    else:
        print("Using existing NEXAL_OPS_SECRET.")
    values["NEXAL_OPS_SECRET"] = ops

    flask = values.get("FLASK_SECRET_KEY")
    if not is_usable_flask_secret(flask, dev_flask_secret):
        flask = systemd_env.get("FLASK_SECRET_KEY")
    if not is_usable_flask_secret(flask, dev_flask_secret):
        flask = systemd_env.get("SECRET_KEY")
    if not is_usable_flask_secret(flask, dev_flask_secret):
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
            f"ERROR: SSO_SECRET_KEY is missing from {env_file} and systemd.\n"
            f"Set SSO_SECRET_KEY in {env_file} to match Portal LEDGER_SSO_SECRET, then re-run.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("Using existing SSO_SECRET_KEY.")
    values["SSO_SECRET_KEY"] = sso

    write_env_file(env_file, values)

    written = parse_env_file(env_file)
    for required in ("NEXAL_PRODUCTION", "NEXAL_OPS_SECRET", "FLASK_SECRET_KEY", "SSO_SECRET_KEY"):
        if required not in written:
            print(f"FATAL: {required} missing from {env_file} after write.", file=sys.stderr)
            sys.exit(1)

    return values


def validate_production_secrets(app_dir: Path, env_file: Path) -> None:
    if not app_dir.is_dir():
        print(f"Skipping Python validation (APP_DIR {app_dir} not found).")
        return
    env = os.environ.copy()
    env["NEXAL_LEDGER_ENV_FILE"] = str(env_file)
    env.setdefault("NEXAL_PRODUCTION", "true")
    env["PYTHONPATH"] = str(app_dir)
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
        cwd=str(app_dir),
    )


def run_health_checks(service: str, ledger_port: str, ops_secret: str, *, skip_service: bool) -> None:
    if not skip_service:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", service], check=True)
        subprocess.run(["systemctl", "is-active", "--quiet", service], check=True)

    root = subprocess.run(
        [
            "curl",
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "--max-time",
            "10",
            f"http://127.0.0.1:{ledger_port}/",
        ],
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
            f"http://127.0.0.1:{ledger_port}/api/ops/backup-health",
        ],
        capture_output=True,
        text=True,
    )

    print("")
    print("Ledger service restarted." if not skip_service else "Configure step completed.")
    print(f"Local root HTTP status: {root.stdout.strip() or 'unavailable'}")
    print(f"Local backup-health HTTP status: {health.stdout.strip() or 'unavailable'}")
    print("")
    print("Set the SAME value on Vercel (Portal production):")
    print(f"  NEXAL_OPS_SECRET={ops_secret}")
    print("")

    if health.stdout.strip() != "200":
        print(
            f"WARNING: Local backup-health did not return HTTP 200. Check: journalctl -u {service} -n 50 --no-pager",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure Ledger production secrets.")
    parser.add_argument("--skip-service", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    env_file = Path(os.environ.get("NEXAL_LEDGER_ENV_FILE", "/etc/nexal-ledger.env"))
    service = os.environ.get("SERVICE", "nexal-ledger")
    service_file = Path(os.environ.get("SERVICE_FILE", f"/etc/systemd/system/{service}.service"))
    dropin_dir = Path(os.environ.get("DROPIN_DIR", f"/etc/systemd/system/{service}.service.d"))
    dropin_file = Path(os.environ.get("DROPIN_FILE", str(dropin_dir / "99-nexal-env.conf")))
    dev_flask_secret = os.environ.get(
        "DEV_FLASK_SECRET",
        "sra-compliant-secret-key-change-in-production",
    )
    app_dir = Path(os.environ.get("APP_DIR", "/opt/nexal-ledger"))
    ledger_port = os.environ.get("LEDGER_PORT", "5001")

    env_file.parent.mkdir(parents=True, exist_ok=True)
    if not env_file.exists():
        env_file.touch()
    os.chmod(env_file, 0o600)

    values = ensure_values(env_file, service_file, service, dev_flask_secret)

    if service_file.is_file() and scrub_dev_flask_from_unit(service_file, dev_flask_secret):
        print(f"Removed dev FLASK/SECRET defaults from {service_file}.")

    ensure_dropin_env_file(dropin_dir, dropin_file, env_file)
    print(f"Wrote systemd drop-in {dropin_file} (EnvironmentFile loads last).")

    validate_production_secrets(app_dir, env_file)
    run_health_checks(service, ledger_port, values["NEXAL_OPS_SECRET"], skip_service=args.skip_service)


if __name__ == "__main__":
    main()
