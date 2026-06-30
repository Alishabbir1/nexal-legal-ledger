"""Tests for ops secret resolution."""
import os
import tempfile

from nexal_platform.ops_secret import (
    OPS_SECRET_ENV_KEY,
    OPS_SECRET_HEADER,
    bootstrap_ops_secret_env,
    get_expected_ops_secret,
    get_provided_ops_secret,
    normalize_ops_secret,
    validate_ops_secret_value,
)


def test_normalize_ops_secret_strips_quotes():
    assert normalize_ops_secret('"abc123456789012"') == "abc123456789012"
    assert normalize_ops_secret("'abc123456789012'") == "abc123456789012"


def test_validate_ops_secret_value_rejects_placeholder():
    assert validate_ops_secret_value("changeme") is not None
    assert validate_ops_secret_value("replace-with-shared-secret") is not None
    assert validate_ops_secret_value("valid-production-secret-value") is None


def test_bootstrap_ops_secret_env_loads_from_env_file(monkeypatch):
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
        handle.write('export NEXAL_OPS_SECRET="abc123456789012"\n')
        path = handle.name

    try:
        monkeypatch.delenv(OPS_SECRET_ENV_KEY, raising=False)
        monkeypatch.setenv("NEXAL_LEDGER_ENV_FILE", path)
        import nexal_platform.ops_secret as ops_secret_module

        ops_secret_module._BOOTSTRAPPED = False
        bootstrap_ops_secret_env()
        assert os.environ[OPS_SECRET_ENV_KEY] == "abc123456789012"
        assert get_expected_ops_secret() == "abc123456789012"
    finally:
        os.remove(path)


def test_get_provided_ops_secret_normalizes_header():
    class Headers:
        def get(self, key, default=None):
            return '"header-secret-value"' if key == OPS_SECRET_HEADER else default

    assert get_provided_ops_secret(Headers()) == "header-secret-value"


def test_ops_secret_header_name():
    assert OPS_SECRET_HEADER == "X-Nexal-Ops-Secret"
