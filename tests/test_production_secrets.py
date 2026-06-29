"""Tests for production secret validation."""
import pytest

from nexal_platform.production_secrets import (
    DEV_FLASK_SECRET,
    DEV_SSO_SECRET,
    validate_production_secrets,
)


def test_validate_production_secrets_allows_custom_values(monkeypatch):
    monkeypatch.setenv("NEXAL_PRODUCTION", "true")
    validate_production_secrets(
        sso_secret="unique-sso-secret-for-production",
        flask_secret="unique-flask-secret-for-production",
    )


def test_validate_production_secrets_rejects_dev_defaults(monkeypatch):
    monkeypatch.setenv("NEXAL_PRODUCTION", "true")
    with pytest.raises(SystemExit):
        validate_production_secrets(sso_secret=DEV_SSO_SECRET, flask_secret=DEV_FLASK_SECRET)


def test_validate_production_secrets_skips_non_production(monkeypatch):
    monkeypatch.delenv("NEXAL_PRODUCTION", raising=False)
    validate_production_secrets(sso_secret=DEV_SSO_SECRET, flask_secret=DEV_FLASK_SECRET)
