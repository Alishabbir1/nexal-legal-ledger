"""Tests for ops secret resolution."""
import os
import tempfile

from nexal_platform.ops_secret import get_expected_ops_secret


def test_get_expected_ops_secret_from_env_file():
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
        handle.write('NEXAL_OPS_SECRET="abc123"\n')
        path = handle.name

    try:
        os.environ["NEXAL_LEDGER_ENV_FILE"] = path
        os.environ.pop("NEXAL_OPS_SECRET", None)
        assert get_expected_ops_secret() == "abc123"
    finally:
        os.environ.pop("NEXAL_LEDGER_ENV_FILE", None)
        os.remove(path)


def test_get_expected_ops_secret_falls_back_to_env_file(monkeypatch):
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
        handle.write("NEXAL_OPS_SECRET=file-secret\n")
        path = handle.name

    try:
        monkeypatch.delenv("NEXAL_OPS_SECRET", raising=False)
        monkeypatch.delenv("LEDGER_OPS_SECRET", raising=False)
        monkeypatch.delenv("BACKUP_HEALTH_SECRET", raising=False)
        monkeypatch.setenv("NEXAL_LEDGER_ENV_FILE", path)
        assert get_expected_ops_secret() == "file-secret"
    finally:
        os.remove(path)
