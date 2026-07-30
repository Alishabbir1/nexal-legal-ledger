"""Regression tests — light/dark theme must inherit from a single source of truth."""
import os
import re

import pytest

import app as app_module
from app import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def client(monkeypatch):
    from nexal_platform import session_security

    monkeypatch.setattr(
        session_security, "validate_sso_session_binding", lambda *a, **kw: None
    )
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _login(client):
    admin = app_module.db.get_user_by_username("admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin["user_id"] if admin else 1
        sess["username"] = "admin"
        sess["role"] = "admin"
        sess["sso_login"] = True


def test_base_template_uses_theme_tokens_not_hardcoded_body():
    path = os.path.join(ROOT, "templates", "base.html")
    html = open(path, encoding="utf-8").read()
    assert 'style="background-color:#070a14' not in html
    assert "background: var(--bg-base)" in html
    assert "html[data-theme=\"light\"]" in html
    assert "localStorage.getItem('ss-theme')" in html


def test_enterprise_css_defines_light_theme_and_app_main_background():
    path = os.path.join(ROOT, "static", "css", "enterprise.css")
    css = open(path, encoding="utf-8").read()
    assert '[data-theme="light"]' in css
    assert ".app-main" in css
    assert re.search(r"\.app-main\s*\{[^}]*background:\s*var\(--bg-base\)", css, re.S)


def test_portal_pages_do_not_hardcode_dark_page_background(client):
    _login(client)
    pages = [
        "/",
        "/client-ledger",
        "/cashbook",
        "/office-account",
        "/reconciliation",
        "/reports",
        "/user-management",
        "/audit-log",
        "/admin/security",
        "/system-backups",
    ]
    for path in pages:
        resp = client.get(path, follow_redirects=True)
        assert resp.status_code == 200, f"{path} → {resp.status_code}"
        body = resp.get_data(as_text=True)
        assert 'style="background-color:#070a14' not in body
        assert "background-color:#070a14" not in body.replace(" ", "")
        assert "var(--bg-base)" in body


@pytest.mark.parametrize(
    "template",
    ["office_account.html", "cashbook.html"],
)
def test_confirm_modals_use_shared_styles_not_hardcoded_dark(template):
    path = os.path.join(ROOT, "templates", template)
    content = open(path, encoding="utf-8").read()
    assert "background: #0c1120" not in content
    assert "modal-box" in content
