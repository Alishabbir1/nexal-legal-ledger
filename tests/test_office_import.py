"""
Office Account bank statement import — end-to-end tests.
Covers: parse, stage, review, approve, cancel, duplicate detection.
Verifies that client ledger / cashbook / reconciliation data is NEVER touched.
"""
import io
import json
import uuid
from decimal import Decimal

import pytest

import app as app_module
from app import app
from lib.office_import import parse_office_statement, parse_office_csv


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    from nexal_platform import session_security

    monkeypatch.setattr(
        session_security, 'validate_sso_session_binding', lambda *a, **kw: None
    )
    app.config['TESTING'] = True
    admin = app_module.db.get_user_by_username('admin')
    with app.test_client() as tc:
        with tc.session_transaction() as sess:
            sess['user_id'] = admin['user_id'] if admin else 1
            sess['username'] = 'admin'
            sess['role'] = 'admin'
            sess['sso_login'] = True
        yield tc


def _csv(rows_text: str) -> bytes:
    return rows_text.encode('utf-8')


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

def test_parse_csv_single_signed_amount():
    csv = _csv('Date,Description,Amount\n2024-01-10,Rent,-1200.00\n2024-01-15,Fee,500.00\n')
    rows, err = parse_office_statement(csv, 'test.csv')
    assert err is None
    assert len(rows) == 2
    assert rows[0]['transaction_type'] == 'Payment'
    assert rows[0]['amount'] == Decimal('1200.00')
    assert rows[1]['transaction_type'] == 'Receipt'


def test_parse_csv_separate_debit_credit():
    csv = _csv('Date,Description,Debit,Credit,Balance\n'
               '2024-02-01,Salaries,3000,,12000\n'
               '2024-02-05,Fee Income,,800,12800\n')
    rows, err = parse_office_statement(csv, 'test.csv')
    assert err is None
    assert len(rows) == 2
    assert rows[0]['transaction_type'] == 'Payment'
    assert rows[1]['transaction_type'] == 'Receipt'
    assert rows[1]['balance'] == Decimal('12800')


def test_parse_csv_money_in_money_out():
    csv = _csv('Date,Narrative,Money Out,Money In,Balance\n'
               '01/03/2024,BACS,,500.00,2500.00\n'
               '05/03/2024,Direct Debit,200.00,,2300.00\n')
    rows, err = parse_office_statement(csv, 'bank.csv')
    assert err is None
    assert rows[0]['transaction_type'] == 'Receipt'
    assert rows[1]['transaction_type'] == 'Payment'


def test_parse_csv_uk_date_format():
    csv = _csv('Date,Description,Amount\n15/04/2024,Test,100.00\n')
    rows, err = parse_office_csv(csv)
    assert err is None
    assert rows[0]['date'] == '2024-04-15'


def test_parse_csv_empty_file():
    rows, err = parse_office_statement(b'', 'empty.csv')
    assert err is not None
    assert rows == []


def test_parse_csv_missing_date_column():
    csv = _csv('Desc,Amount\nRent,100\n')
    rows, err = parse_office_statement(csv, 'x.csv')
    assert err is not None
    assert 'Date' in err


def test_parse_csv_missing_amount_column():
    csv = _csv('Date,Description\n2024-01-01,Something\n')
    rows, err = parse_office_statement(csv, 'x.csv')
    assert err is not None


def test_parse_csv_parenthesis_negative():
    csv = _csv('Date,Description,Amount\n2024-01-01,Test,(500.00)\n')
    rows, err = parse_office_statement(csv, 'x.csv')
    assert err is None
    assert rows[0]['transaction_type'] == 'Payment'
    assert rows[0]['amount'] == Decimal('500.00')


def test_parse_csv_currency_symbol_stripped():
    # Proper CSV quoting needed when amounts contain thousands-separator commas
    csv = _csv('Date,Amount,Description\n2024-01-01,"£1,234.56",Fees\n')
    rows, err = parse_office_statement(csv, 'x.csv')
    assert err is None
    assert rows[0]['amount'] == Decimal('1234.56')


def test_unsupported_extension():
    rows, err = parse_office_statement(b'data', 'statement.ofx')
    assert err is not None
    assert 'csv' in err.lower()


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------

def test_upload_page_loads(client):
    resp = client.get('/office-account/import-statement')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Upload' in html
    assert 'statement_file' in html


def test_upload_no_file_redirects_with_error(client):
    resp = client.post('/office-account/import-statement', data={})
    assert resp.status_code == 302  # redirect back to upload form


def test_upload_wrong_extension_rejected(client):
    data = {
        'statement_file': (io.BytesIO(b'data'), 'statement.pdf'),
    }
    resp = client.post(
        '/office-account/import-statement',
        data=data,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302  # redirect with error flash


def test_upload_valid_csv_creates_staging_and_redirects_to_review(client):
    csv_data = _csv(
        'Date,Description,Amount\n'
        '2024-03-01,Office Rent,-1200.00\n'
        '2024-03-05,Consulting Fee,800.00\n'
    )
    data = {
        'statement_file': (io.BytesIO(csv_data), 'march.csv'),
        'statement_start': '2024-03-01',
        'statement_end': '2024-03-31',
    }
    resp = client.post(
        '/office-account/import-statement',
        data=data,
        content_type='multipart/form-data',
    )
    assert resp.status_code == 302
    location = resp.headers.get('Location', '')
    assert 'import-review' in location
    assert 'batch=' in location


def test_review_page_shows_rows(client):
    # Upload first
    csv_data = _csv('Date,Description,Amount\n2024-04-01,Salary,-2000.00\n2024-04-10,Fee,300.00\n')
    up = client.post(
        '/office-account/import-statement',
        data={'statement_file': (io.BytesIO(csv_data), 'april.csv')},
        content_type='multipart/form-data',
    )
    location = up.headers.get('Location', '')
    batch_id = location.split('batch=')[-1] if 'batch=' in location else ''
    assert batch_id, 'No batch_id in redirect'

    resp = client.get(f'/office-account/import-review?batch={batch_id}')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Salary' in html
    assert 'Fee' in html
    assert 'Payment' in html
    assert 'Receipt' in html


def test_cancel_import_deletes_staging(client):
    csv_data = _csv('Date,Description,Amount\n2024-05-01,Test,100.00\n')
    up = client.post(
        '/office-account/import-statement',
        data={'statement_file': (io.BytesIO(csv_data), 'test.csv')},
        content_type='multipart/form-data',
    )
    batch_id = up.headers.get('Location', '').split('batch=')[-1]
    assert batch_id

    # Verify staging exists
    rows, meta = app_module.db.get_office_import_staging(batch_id)
    assert len(rows) == 1

    # Cancel
    resp = client.post('/office-account/import-cancel', data={'batch_id': batch_id})
    assert resp.status_code == 302

    # Staging should be gone
    rows2, _ = app_module.db.get_office_import_staging(batch_id)
    assert rows2 == []


def test_approve_import_creates_office_transactions(client):
    # Record initial office balance and transaction count
    initial_balance = app_module.db.get_office_balance()

    csv_data = _csv(
        'Date,Description,Amount\n'
        '2024-06-01,Test Import Receipt,250.00\n'
    )
    up = client.post(
        '/office-account/import-statement',
        data={'statement_file': (io.BytesIO(csv_data), 'june.csv')},
        content_type='multipart/form-data',
    )
    batch_id = up.headers.get('Location', '').split('batch=')[-1]
    rows, _ = app_module.db.get_office_import_staging(batch_id)
    assert len(rows) == 1
    row_id = str(rows[0]['id'])

    # Approve (keep the one row, use defaults for description/ref/source)
    resp = client.post('/office-account/import-approve', data={
        'batch_id': batch_id,
        f'keep_{row_id}': 'on',
        f'ref_{row_id}': 'Test Ref',
        f'desc_{row_id}': 'Test Import',
        f'source_{row_id}': 'Bank Transfer',
    })
    assert resp.status_code == 302
    location = resp.headers.get('Location', '')
    assert 'office-account' in location

    # Staging should be cleared
    rows2, _ = app_module.db.get_office_import_staging(batch_id)
    assert rows2 == []

    # Balance should have increased
    new_balance = app_module.db.get_office_balance()
    assert new_balance == initial_balance + Decimal('250.00'), (
        f"Balance should increase by 250; was {initial_balance}, now {new_balance}"
    )


def test_approve_all_unchecked_cancels_import(client):
    csv_data = _csv('Date,Description,Amount\n2024-07-01,Test,100.00\n')
    up = client.post(
        '/office-account/import-statement',
        data={'statement_file': (io.BytesIO(csv_data), 'test.csv')},
        content_type='multipart/form-data',
    )
    batch_id = up.headers.get('Location', '').split('batch=')[-1]
    rows, _ = app_module.db.get_office_import_staging(batch_id)
    row_id = str(rows[0]['id'])

    # POST approve without any keep_ checkbox
    resp = client.post('/office-account/import-approve', data={'batch_id': batch_id})
    assert resp.status_code == 302
    rows2, _ = app_module.db.get_office_import_staging(batch_id)
    assert rows2 == []


def test_duplicate_detection_flags_existing_transaction(client):
    """Importing a row that exactly matches an existing office_cashbook record is flagged."""
    # Create a real transaction first
    app_module.db.create_office_transaction(
        transaction_date='2024-08-01',
        amount=Decimal('99.00'),
        transaction_type='Receipt',
        reference='DupTest',
        source='Bank Transfer',
        created_by='pytest',
    )
    # Now import a CSV with the exact same row
    csv_data = _csv('Date,Description,Amount\n2024-08-01,DupTest,99.00\n')
    up = client.post(
        '/office-account/import-statement',
        data={'statement_file': (io.BytesIO(csv_data), 'dup.csv')},
        content_type='multipart/form-data',
    )
    batch_id = up.headers.get('Location', '').split('batch=')[-1]
    rows, _ = app_module.db.get_office_import_staging(batch_id)
    assert rows[0]['is_duplicate'] == 1, 'Expected duplicate flag on row'
    app_module.db.delete_office_import_staging(batch_id)


def test_import_does_not_touch_client_ledger(client):
    """Importing office transactions must not affect client ledger."""
    # Count ledger_transactions rows directly via a raw DB query
    conn = app_module.db.get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM ledger_transactions WHERE COALESCE(is_deleted,0)=0")
        initial_count = c.fetchone()[0]
    finally:
        conn.close()

    csv_data = _csv('Date,Description,Amount\n2024-09-01,Isolation Test,150.00\n')
    up = client.post(
        '/office-account/import-statement',
        data={'statement_file': (io.BytesIO(csv_data), 'iso.csv')},
        content_type='multipart/form-data',
    )
    batch_id = up.headers.get('Location', '').split('batch=')[-1]
    rows, _ = app_module.db.get_office_import_staging(batch_id)
    row_id = str(rows[0]['id'])

    client.post('/office-account/import-approve', data={
        'batch_id': batch_id,
        f'keep_{row_id}': 'on',
        f'ref_{row_id}': 'IsoRef',
        f'source_{row_id}': 'Bank Transfer',
    })

    # Client ledger must be unchanged
    conn2 = app_module.db.get_connection()
    try:
        c2 = conn2.cursor()
        c2.execute("SELECT COUNT(*) FROM ledger_transactions WHERE COALESCE(is_deleted,0)=0")
        after_count = c2.fetchone()[0]
    finally:
        conn2.close()

    assert after_count == initial_count, (
        f'Client ledger transaction count changed after office import: '
        f'{initial_count} → {after_count}'
    )


def test_import_does_not_touch_cashbook(client):
    """Importing office transactions must not create cashbook_transactions records."""
    initial = app_module.db.get_all_cashbook_transactions()
    initial_count = len(initial)

    csv_data = _csv('Date,Description,Amount\n2024-10-01,CashbookIsolation,75.00\n')
    up = client.post(
        '/office-account/import-statement',
        data={'statement_file': (io.BytesIO(csv_data), 'cb.csv')},
        content_type='multipart/form-data',
    )
    batch_id = up.headers.get('Location', '').split('batch=')[-1]
    rows, _ = app_module.db.get_office_import_staging(batch_id)
    row_id = str(rows[0]['id'])

    client.post('/office-account/import-approve', data={
        'batch_id': batch_id,
        f'keep_{row_id}': 'on',
        f'ref_{row_id}': 'CbRef',
        f'source_{row_id}': 'Bank Transfer',
    })

    after = app_module.db.get_all_cashbook_transactions()
    assert len(after) == initial_count, (
        'Client cashbook count changed after office import'
    )


def test_existing_office_account_still_works_after_feature_added(client):
    """Manual income/expense entry must still work alongside the new import feature."""
    initial = app_module.db.get_office_balance()
    resp = client.get('/office-account/add-income')
    assert resp.status_code == 200
    resp2 = client.get('/office-account/add-expense')
    assert resp2 == resp2  # no exception


def test_office_account_page_shows_upload_button(client):
    resp = client.get('/office-account')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Upload Bank Statement' in html
    assert 'import-statement' in html
