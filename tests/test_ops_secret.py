"""Tests for ops secret resolution."""
import os
import tempfile

import pytest

from nexal_platform.ops_secret import (
    DEV_FLASK_SECRET,
    OPS_SECRET_ENV_KEY,
    OPS_SECRET_HEADER,
    bootstrap_ledger_env,
    bootstrap_ops_secret_env,
    get_expected_ops_secret,
    get_flask_secret,
    get_provided_ops_secret,
    is_usable_flask_secret,
    normalize_ops_secret,
    validate_ops_secret_value,
)
import nexal_platform.ops_secret as ops_secret_module


def test_normalize_ops_secret_strips_quotes():
    assert normalize_ops_secret('"abc123456789012"') == "abc123456789012"
    assert normalize_ops_secret("'abc123456789012'") == "abc123456789012"


def test_validate_ops_secret_value_rejects_placeholder():
    assert validate_ops_secret_value("changeme") is not None
    assert validate_ops_secret_value("replace-with-shared-secret") is not None
    assert validate_ops_secret_value("valid-production-secret-value") is None


def test_is_usable_flask_secret_rejects_dev_default():
    assert is_usable_flask_secret(DEV_FLASK_SECRET) is False
    assert is_usable_flask_secret("unique-flask-secret-for-production") is True


def test_bootstrap_prefers_env_file_flask_secret_over_systemd_dev_default(monkeypatch, tmp_path):
    env_file = tmp_path / "ledger.env"
    env_file.write_text(
        "NEXAL_OPS_SECRET=valid-production-secret-value\n"
        "FLASK_SECRET_KEY=unique-flask-secret-for-production\n"
        "SSO_SECRET_KEY=unique-sso-secret-for-production\n",
        encoding="utf-8",
    )
    service_file = tmp_path / "nexal-ledger.service"
    service_file.write_text(
        f"[Service]\nEnvironment=FLASK_SECRET_KEY={DEV_FLASK_SECRET}\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("NEXAL_LEDGER_ENV_FILE", str(env_file))
    monkeypatch.setenv("FLASK_SECRET_KEY", DEV_FLASK_SECRET)
    monkeypatch.setattr(ops_secret_module, "DEFAULT_ENV_FILE_PATHS", ())
    monkeypatch.setattr(ops_secret_module, "SYSTEMD_UNIT_PATHS", (str(service_file),))

    bootstrap_ledger_env()
    assert get_flask_secret() == "unique-flask-secret-for-production"
    assert os.environ["FLASK_SECRET_KEY"] == "unique-flask-secret-for-production"


def test_bootstrap_strips_dev_flask_from_environ_when_env_file_has_production_key(monkeypatch, tmp_path):
    env_file = tmp_path / "ledger.env"
    env_file.write_text(
        "NEXAL_PRODUCTION=true\n"
        "NEXAL_OPS_SECRET=valid-production-secret-value\n"
        "FLASK_SECRET_KEY=unique-flask-secret-for-production\n"
        "SSO_SECRET_KEY=unique-sso-secret-for-production\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NEXAL_LEDGER_ENV_FILE", str(env_file))
    monkeypatch.setenv("NEXAL_PRODUCTION", "true")
    monkeypatch.setenv("FLASK_SECRET_KEY", DEV_FLASK_SECRET)
    monkeypatch.setenv("SECRET_KEY", DEV_FLASK_SECRET)
    monkeypatch.setattr(ops_secret_module, "DEFAULT_ENV_FILE_PATHS", ())

    bootstrap_ledger_env()
    assert get_flask_secret() == "unique-flask-secret-for-production"
    assert os.environ["FLASK_SECRET_KEY"] != DEV_FLASK_SECRET


def test_bootstrap_does_not_keep_dev_flask_in_environ_without_production_key(monkeypatch):
    monkeypatch.setenv("NEXAL_PRODUCTION", "true")
    monkeypatch.setenv("FLASK_SECRET_KEY", DEV_FLASK_SECRET)
    monkeypatch.setenv("SECRET_KEY", DEV_FLASK_SECRET)
    monkeypatch.setattr(ops_secret_module, "DEFAULT_ENV_FILE_PATHS", ())
    monkeypatch.setattr(ops_secret_module, "SYSTEMD_UNIT_PATHS", ())

    bootstrap_ledger_env()
    assert "FLASK_SECRET_KEY" not in os.environ
    assert "SECRET_KEY" not in os.environ
    assert get_flask_secret() == ""


def test_bootstrap_ops_secret_env_loads_from_env_file(monkeypatch):
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
        handle.write('export NEXAL_OPS_SECRET="abc123456789012"\n')
        path = handle.name

    try:
        monkeypatch.delenv(OPS_SECRET_ENV_KEY, raising=False)
        monkeypatch.setenv("NEXAL_LEDGER_ENV_FILE", path)
        bootstrap_ops_secret_env()
        assert os.environ[OPS_SECRET_ENV_KEY] == "abc123456789012"
        assert get_expected_ops_secret() == "abc123456789012"
    finally:
        os.remove(path)


def test_bootstrap_ledger_env_loads_flask_secret_from_env_file(monkeypatch):
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
        handle.write(
            "NEXAL_OPS_SECRET=valid-production-secret-value\n"
            "FLASK_SECRET_KEY=unique-flask-secret-for-production\n"
            "SSO_SECRET_KEY=unique-sso-secret-for-production\n"
        )
        path = handle.name

    try:
        monkeypatch.delenv(OPS_SECRET_ENV_KEY, raising=False)
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
        monkeypatch.delenv("SSO_SECRET_KEY", raising=False)
        monkeypatch.setenv("NEXAL_LEDGER_ENV_FILE", path)
        bootstrap_ledger_env()
        assert os.environ["FLASK_SECRET_KEY"] == "unique-flask-secret-for-production"
        assert os.environ["SECRET_KEY"] == "unique-flask-secret-for-production"
        assert os.environ["SSO_SECRET_KEY"] == "unique-sso-secret-for-production"
    finally:
        os.remove(path)


def test_bootstrap_ledger_env_passes_production_validation(monkeypatch):
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
        handle.write(
            "NEXAL_OPS_SECRET=valid-production-secret-value\n"
            "FLASK_SECRET_KEY=unique-flask-secret-for-production\n"
            "SSO_SECRET_KEY=unique-sso-secret-for-production\n"
        )
        path = handle.name

    try:
        from nexal_platform.production_secrets import validate_production_secrets

        monkeypatch.setenv("NEXAL_PRODUCTION", "true")
        monkeypatch.delenv(OPS_SECRET_ENV_KEY, raising=False)
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
        monkeypatch.delenv("SSO_SECRET_KEY", raising=False)
        monkeypatch.setenv("NEXAL_LEDGER_ENV_FILE", path)
        bootstrap_ledger_env()
        validate_production_secrets(
            sso_secret=os.environ.get("SSO_SECRET_KEY"),
            flask_secret=os.environ.get("FLASK_SECRET_KEY"),
            ops_secret=os.environ.get(OPS_SECRET_ENV_KEY),
        )
    finally:
        os.remove(path)


def test_loads_ops_secret_from_systemd_environment_directive(monkeypatch, tmp_path):
    """Production VPS often sets Environment=NEXAL_OPS_SECRET=... inline in the unit file."""
    service_file = tmp_path / "nexal-ledger.service"
    service_file.write_text(
        "[Service]\nEnvironment=NEXAL_OPS_SECRET=techstacbackup2026\n",
        encoding="utf-8",
    )

    monkeypatch.delenv(OPS_SECRET_ENV_KEY, raising=False)
    monkeypatch.setattr(ops_secret_module, "DEFAULT_ENV_FILE_PATHS", ())
    monkeypatch.setattr(ops_secret_module, "SYSTEMD_UNIT_PATHS", (str(service_file),))

    bootstrap_ops_secret_env()
    assert os.environ[OPS_SECRET_ENV_KEY] == "techstacbackup2026"
    assert get_expected_ops_secret() == "techstacbackup2026"


def test_loads_ops_secret_from_quoted_systemd_environment(monkeypatch, tmp_path):
    service_file = tmp_path / "nexal-ledger.service"
    service_file.write_text(
        '[Service]\nEnvironment="NEXAL_OPS_SECRET=techstacbackup2026"\n',
        encoding="utf-8",
    )

    monkeypatch.delenv(OPS_SECRET_ENV_KEY, raising=False)
    monkeypatch.setattr(ops_secret_module, "DEFAULT_ENV_FILE_PATHS", ())
    monkeypatch.setattr(ops_secret_module, "SYSTEMD_UNIT_PATHS", (str(service_file),))

    assert get_expected_ops_secret() == "techstacbackup2026"


def test_get_expected_ops_secret_retries_when_env_empty_after_first_import(monkeypatch, tmp_path):
    """Simulate gunicorn import before env is visible: second call must still resolve."""
    service_file = tmp_path / "nexal-ledger.service"
    service_file.write_text(
        "[Service]\nEnvironment=NEXAL_OPS_SECRET=techstacbackup2026\n",
        encoding="utf-8",
    )

    monkeypatch.delenv(OPS_SECRET_ENV_KEY, raising=False)
    monkeypatch.setattr(ops_secret_module, "DEFAULT_ENV_FILE_PATHS", ())
    monkeypatch.setattr(ops_secret_module, "SYSTEMD_UNIT_PATHS", (str(service_file),))

    assert get_expected_ops_secret() == "techstacbackup2026"
    assert get_expected_ops_secret() == "techstacbackup2026"


def test_get_provided_ops_secret_normalizes_header():
    class Headers:
        def get(self, key, default=None):
            return '"header-secret-value"' if key == OPS_SECRET_HEADER else default

    assert get_provided_ops_secret(Headers()) == "header-secret-value"


def test_ops_secret_header_name():
    assert OPS_SECRET_HEADER == "X-Nexal-Ops-Secret"


def test_backup_health_api_with_systemd_resolved_secret(monkeypatch, tmp_path):
    """End-to-end: secret only in systemd unit file, not in os.environ at import."""
    service_file = tmp_path / "nexal-ledger.service"
    secret = "techstacbackup2026"
    service_file.write_text(
        f"[Service]\nEnvironment=NEXAL_OPS_SECRET={secret}\n",
        encoding="utf-8",
    )

    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("NEXAL_DATA_DIR", str(data_root))
    monkeypatch.delenv(OPS_SECRET_ENV_KEY, raising=False)
    monkeypatch.setattr(ops_secret_module, "DEFAULT_ENV_FILE_PATHS", ())
    monkeypatch.setattr(ops_secret_module, "SYSTEMD_UNIT_PATHS", (str(service_file),))

    import app as ledger_app

    client = ledger_app.app.test_client()
    response = client.get(
        "/api/ops/backup-health",
        headers={"X-Nexal-Ops-Secret": secret},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["system"] == "ledger"
