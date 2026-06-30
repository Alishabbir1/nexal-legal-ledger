"""Tests for scripts/configure_production_env.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from configure_production_env import (  # noqa: E402
    ensure_values,
    main,
    read_systemd_environment,
    scrub_dev_flask_from_unit,
)

DEV_FLASK = "sra-compliant-secret-key-change-in-production"


def test_read_systemd_environment_reads_dropin_conf(tmp_path: Path):
    service_file = tmp_path / "nexal-ledger.service"
    service_file.write_text("[Service]\nEnvironment=NEXAL_OPS_SECRET=ops-from-unit\n", encoding="utf-8")
    dropin_dir = tmp_path / "nexal-ledger.service.d"
    dropin_dir.mkdir()
    (dropin_dir / "override.conf").write_text(
        "[Service]\nEnvironment=SSO_SECRET_KEY=sso-from-dropin\n",
        encoding="utf-8",
    )

    env = read_systemd_environment(service_file, "nexal-ledger")
    assert env["NEXAL_OPS_SECRET"] == "ops-from-unit"
    assert env["SSO_SECRET_KEY"] == "sso-from-dropin"


def test_ensure_values_writes_flask_secret_key(tmp_path: Path):
    env_file = tmp_path / "nexal-ledger.env"
    env_file.write_text(
        "NEXAL_PRODUCTION=true\nNEXAL_OPS_SECRET=existing-ops-secret-value\n",
        encoding="utf-8",
    )
    service_file = tmp_path / "nexal-ledger.service"
    service_file.write_text(
        f"[Service]\nEnvironment=FLASK_SECRET_KEY={DEV_FLASK} SSO_SECRET_KEY=existing-sso-secret-value\n",
        encoding="utf-8",
    )

    values = ensure_values(env_file, service_file, "nexal-ledger", DEV_FLASK)

    assert "FLASK_SECRET_KEY" in values
    assert len(values["FLASK_SECRET_KEY"]) >= 32
    assert values["FLASK_SECRET_KEY"] != DEV_FLASK

    written = env_file.read_text(encoding="utf-8")
    assert "FLASK_SECRET_KEY=" in written
    assert "SSO_SECRET_KEY=existing-sso-secret-value" in written
    assert "NEXAL_OPS_SECRET=existing-ops-secret-value" in written


def test_scrub_dev_flask_from_unit(tmp_path: Path):
    service_file = tmp_path / "nexal-ledger.service"
    service_file.write_text(
        f"[Service]\nEnvironment=FLASK_SECRET_KEY={DEV_FLASK} NEXAL_DATA_DIR=/var/lib/nexal-legal\n",
        encoding="utf-8",
    )

    assert scrub_dev_flask_from_unit(service_file, DEV_FLASK) is True
    updated = service_file.read_text(encoding="utf-8")
    assert DEV_FLASK not in updated
    assert "NEXAL_DATA_DIR=/var/lib/nexal-legal" in updated


def test_configure_production_env_main_completes_locally(tmp_path: Path, monkeypatch):
    env_file = tmp_path / "nexal-ledger.env"
    env_file.write_text(
        "NEXAL_PRODUCTION=true\nNEXAL_OPS_SECRET=existing-ops-secret-value\n",
        encoding="utf-8",
    )
    service_file = tmp_path / "nexal-ledger.service"
    service_file.write_text(
        f"[Service]\nEnvironment=FLASK_SECRET_KEY={DEV_FLASK} SSO_SECRET_KEY=existing-sso-secret-value\n",
        encoding="utf-8",
    )
    dropin_dir = tmp_path / "nexal-ledger.service.d"

    monkeypatch.setenv("NEXAL_LEDGER_ENV_FILE", str(env_file))
    monkeypatch.setenv("SERVICE", "nexal-ledger")
    monkeypatch.setenv("SERVICE_FILE", str(service_file))
    monkeypatch.setenv("DROPIN_DIR", str(dropin_dir))
    monkeypatch.setenv("DROPIN_FILE", str(dropin_dir / "99-nexal-env.conf"))
    monkeypatch.setenv("APP_DIR", str(REPO_ROOT))
    monkeypatch.setenv("LEDGER_PORT", "59999")
    monkeypatch.setenv("DEV_FLASK_SECRET", DEV_FLASK)
    monkeypatch.setattr(sys, "argv", ["configure_production_env.py", "--skip-service"])

    with patch("configure_production_env.run_health_checks") as health_mock:
        main()

    written = env_file.read_text(encoding="utf-8")
    assert "FLASK_SECRET_KEY=" in written
    assert DEV_FLASK not in service_file.read_text(encoding="utf-8")
    dropin = (dropin_dir / "99-nexal-env.conf").read_text(encoding="utf-8")
    assert "Environment=FLASK_SECRET_KEY=" in dropin
    assert "Environment=SECRET_KEY=" in dropin
    assert DEV_FLASK not in dropin
    health_mock.assert_called_once()
