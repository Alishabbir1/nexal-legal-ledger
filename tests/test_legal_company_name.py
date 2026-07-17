"""Customer-facing legal company name branding tests."""
import re

import pytest

import app as app_module
from app import app, build_pdf_report
from lib.branding import LEGAL_COMPANY_NAME, PRODUCT_NAME


@pytest.fixture()
def client(monkeypatch):
    from nexal_platform import session_security

    monkeypatch.setattr(session_security, "validate_sso_session_binding", lambda *args, **kwargs: None)
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_branding_constants():
    assert PRODUCT_NAME == "Nexal Legal"
    assert LEGAL_COMPANY_NAME == "Nexal Solutions Ltd"


def test_context_processor_exposes_legal_company_name():
    with app.app_context():
        with app.test_request_context():
            ctx = app_module.inject_user()
            assert ctx["legal_company_name"] == "Nexal Solutions Ltd"
            assert ctx["product_name"] == "Nexal Legal"


def test_base_template_renders_legal_company_name(client):
    admin = app_module.db.get_user_by_username("admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin["user_id"] if admin else 1
        sess["username"] = "admin"
        sess["role"] = "admin"
        sess["sso_login"] = True

    resp = client.get("/reports")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200, html[:500]
    assert "Nexal Solutions Ltd" in html
    assert re.search(r"Nexal Solutions(?! Ltd)", html) is None


def test_pdf_builder_uses_legal_company_name_constant():
    source = open(app_module.__file__, encoding="utf-8").read()
    assert "LEGAL_COMPANY_NAME" in source
    assert 'Paragraph("Nexal Solutions"' not in source

    buf = build_pdf_report("Test", "Sub", ["Col"], [["v"]], [100])
    assert buf.getvalue().startswith(b"%PDF")
