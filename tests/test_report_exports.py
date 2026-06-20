"""Report export routes — in-memory download and save_local disk writes."""
import json
import os
import tempfile

import pytest

import app as app_module
from app import OPENPYXL_AVAILABLE, REPORTLAB_AVAILABLE, app, _get_exports_dir


@pytest.fixture()
def client():
    app.config['TESTING'] = True
    admin = app_module.db.get_user_by_username('admin')
    with app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess['user_id'] = admin['user_id'] if admin else 1
            sess['username'] = 'admin'
            sess['role'] = 'admin'
        yield test_client


LEDGER_EXPORTS = [
    ('/reports/export/ledger-pdf', 'application/pdf', b'%PDF'),
    ('/reports/export/ledger-csv', 'text/csv', None),
    (
        '/reports/export/ledger-xlsx',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        b'PK',
    ),
]


@pytest.mark.parametrize('route,mimetype,magic', LEDGER_EXPORTS)
def test_ledger_export_download_in_memory(client, route, mimetype, magic):
    """Browser exports must succeed without writing to a home Documents folder."""
    def _permission_denied_home():
        raise PermissionError(13, 'Permission denied', '/home/nexal')

    original = app_module._get_exports_dir
    app_module._get_exports_dir = _permission_denied_home
    try:
        if route.endswith('-pdf') and not REPORTLAB_AVAILABLE:
            pytest.skip('reportlab not installed')
        if route.endswith('-xlsx') and not OPENPYXL_AVAILABLE:
            pytest.skip('openpyxl not installed')

        resp = client.get(route)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert mimetype in (resp.content_type or '')
        if magic:
            assert resp.data[: len(magic)] == magic
    finally:
        app_module._get_exports_dir = original


@pytest.mark.skipif(not OPENPYXL_AVAILABLE, reason='openpyxl not installed')
def test_ledger_xlsx_save_local_uses_writable_exports_dir(client):
    """save_local writes under the configured exports directory."""
    with tempfile.TemporaryDirectory() as export_dir:
        original = app_module._get_exports_dir
        app_module._get_exports_dir = lambda: export_dir
        try:
            resp = client.get('/reports/export/ledger-xlsx?save_local=1')
            assert resp.status_code == 200
            payload = json.loads(resp.data)
            assert payload.get('success') is True
            filepath = payload.get('filepath', '')
            assert filepath.startswith(export_dir)
            assert os.path.isfile(filepath)
            assert os.path.getsize(filepath) > 0
            assert filepath.endswith('.xlsx')
        finally:
            app_module._get_exports_dir = original


def test_get_exports_dir_uses_data_dir_not_home(monkeypatch):
    """Exports folder lives under app data dir, not expanduser('~')."""
    with tempfile.TemporaryDirectory() as data_dir:
        monkeypatch.setattr(app_module, '_get_data_dir', lambda: data_dir)
        exports_dir = _get_exports_dir()
        assert exports_dir == os.path.join(data_dir, 'exports')
        assert os.path.isdir(exports_dir)
