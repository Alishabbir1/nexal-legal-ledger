"""Regression tests — customer-facing legal company name must be Nexal Solutions Ltd."""
import re

import pytest

import app as app_module
from app import app, build_pdf_report
from lib.branding import LEGAL_COMPANY_NAME, PRODUCT_NAME


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


def test_branding_constants():
    assert PRODUCT_NAME == "Nexal Legal"
    assert LEGAL_COMPANY_NAME == "Nexal Solutions Ltd"


def test_context_processor_exposes_legal_company_name():
    with app.app_context():
        with app.test_request_context():
            ctx = app_module.inject_user()
    assert ctx["legal_company_name"] == "Nexal Solutions Ltd"
    assert ctx["product_name"] == "Nexal Legal"


@pytest.mark.parametrize("path", ["/reports", "/reconciliation", "/client-ledger"])
def test_template_renders_legal_company_name(client, path):
    _login(client)
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} → {resp.status_code}"
    html = resp.get_data(as_text=True)
    assert "Nexal Solutions Ltd" in html, f"{path}: legal name missing"
    # No bare "Nexal Solutions" without the "Ltd" suffix
    assert not re.search(r"Nexal Solutions(?! Ltd)", html), (
        f"{path}: old name still present"
    )


def test_pdf_builder_uses_branding_constant():
    src = open(app_module.__file__, encoding="utf-8").read()
    assert "LEGAL_COMPANY_NAME" in src, "branding constant not imported in app.py"
    assert 'Paragraph("Nexal Solutions"' not in src, "hardcoded old name still in app.py"
    buf = build_pdf_report("Test", "Sub", ["Col"], [["v"]], [100])
    assert buf.getvalue().startswith(b"%PDF")


def test_no_bare_nexal_solutions_in_source_files():
    """All four customer-facing source files must not contain bare Nexal Solutions."""
    files = [
        "templates/base.html",
        "templates/reconciliation.html",
        "app.py",
        "installer.iss",
    ]
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pat = re.compile(r"Nexal Solutions(?! Ltd)")
    for rel in files:
        path = os.path.join(root, rel)
        content = open(path, encoding="utf-8", errors="replace").read()
        matches = pat.findall(content)
        assert not matches, f"{rel}: found bare 'Nexal Solutions' ({len(matches)} time(s))"
