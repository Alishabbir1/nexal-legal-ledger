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
from lib.office_import import parse_office_statement, parse_office_csv, ParseResult


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

    # Clear statement history before each test to prevent cross-test pollution.
    # office_statement_history persists between pytest runs; stale entries would
    # incorrectly classify first-import batches as mismatches.
    conn = app_module.db.get_connection()
    conn.execute('DELETE FROM office_statement_history')
    conn.commit()
    conn.close()

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
    result = parse_office_statement(csv, 'test.csv')
    rows, err = result.rows, result.error
    assert err is None
    assert len(rows) == 2
    assert rows[0]['transaction_type'] == 'Payment'
    assert rows[0]['amount'] == Decimal('1200.00')
    assert rows[1]['transaction_type'] == 'Receipt'


def test_parse_csv_separate_debit_credit():
    csv = _csv('Date,Description,Debit,Credit,Balance\n'
               '2024-02-01,Salaries,3000,,12000\n'
               '2024-02-05,Fee Income,,800,12800\n')
    result = parse_office_statement(csv, 'test.csv')
    rows, err = result.rows, result.error
    assert err is None
    assert len(rows) == 2
    assert rows[0]['transaction_type'] == 'Payment'
    assert rows[1]['transaction_type'] == 'Receipt'
    assert rows[1]['balance'] == Decimal('12800')


def test_parse_csv_money_in_money_out():
    csv = _csv('Date,Narrative,Money Out,Money In,Balance\n'
               '01/03/2024,BACS,,500.00,2500.00\n'
               '05/03/2024,Direct Debit,200.00,,2300.00\n')
    result = parse_office_statement(csv, 'bank.csv')
    rows, err = result.rows, result.error
    assert err is None
    assert rows[0]['transaction_type'] == 'Receipt'
    assert rows[1]['transaction_type'] == 'Payment'


def test_parse_csv_uk_date_format():
    csv = _csv('Date,Description,Amount\n15/04/2024,Test,100.00\n')
    result = parse_office_csv(csv)
    rows, err = result.rows, result.error
    assert err is None
    assert rows[0]['date'] == '2024-04-15'


def test_parse_csv_empty_file():
    result = parse_office_statement(b'', 'empty.csv')
    rows, err = result.rows, result.error
    assert err is not None
    assert rows == []


def test_parse_csv_missing_date_column():
    csv = _csv('Desc,Amount\nRent,100\n')
    result = parse_office_statement(csv, 'x.csv')
    rows, err = result.rows, result.error
    assert err is not None
    assert 'Date' in err


def test_parse_csv_missing_amount_column():
    csv = _csv('Date,Description\n2024-01-01,Something\n')
    result = parse_office_statement(csv, 'x.csv')
    rows, err = result.rows, result.error
    assert err is not None


def test_parse_csv_parenthesis_negative():
    csv = _csv('Date,Description,Amount\n2024-01-01,Test,(500.00)\n')
    result = parse_office_statement(csv, 'x.csv')
    rows, err = result.rows, result.error
    assert err is None
    assert rows[0]['transaction_type'] == 'Payment'
    assert rows[0]['amount'] == Decimal('500.00')


def test_parse_csv_currency_symbol_stripped():
    # Proper CSV quoting needed when amounts contain thousands-separator commas
    csv = _csv('Date,Amount,Description\n2024-01-01,"£1,234.56",Fees\n')
    result = parse_office_statement(csv, 'x.csv')
    rows, err = result.rows, result.error
    assert err is None
    assert rows[0]['amount'] == Decimal('1234.56')


def test_unsupported_extension():
    result = parse_office_statement(b'data', 'statement.ofx')
    assert result.error is not None
    assert 'csv' in result.error.lower()


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


# ---------------------------------------------------------------------------
# Amount sign regression tests — the bug: office_account.html displayed
# all transactions as positive because it checked `amount|float >= 0`
# instead of using transaction_type.  These tests pin the correct behaviour.
# ---------------------------------------------------------------------------

def _full_csv() -> bytes:
    """
    Test CSV matching the user's regression scenario:
    Receipt £3,500  /  Payments: £2,200 / £49.99 / £89.50 / £8,500 / £186.75 / £950
    Receipts: £1,850 / £4,200
    """
    return (
        b"Date,Description,Money Out,Money In\r\n"
        b"2024-01-01,Opening Balance,,0.00\r\n"
        b"2024-01-02,Client Fee Receipt,,3500.00\r\n"
        b"2024-01-03,Office Rent,2200.00,\r\n"
        b"2024-01-04,Microsoft 365 Subscription,49.99,\r\n"
        b"2024-01-05,Consulting Income,,1850.00\r\n"
        b"2024-01-06,Internet Bill,89.50,\r\n"
        b"2024-01-07,Monthly Staff Salaries,8500.00,\r\n"
        b"2024-01-08,Client Settlement,,4200.00\r\n"
        b"2024-01-09,Professional Indemnity,186.75,\r\n"
        b"2024-01-10,Software Licence,950.00,\r\n"
    )


def test_import_amount_signs_preserved_in_office_account_page(client):
    """
    After approve-import, the Office Account page must show:
      - Payment rows with a leading '-'
      - Receipt rows with a leading '+'
    The sign must come from transaction_type, not from the stored numeric value.
    """
    # Upload + review
    resp = client.post(
        '/office-account/import-statement',
        data={
            'statement_file': (io.BytesIO(_full_csv()), 'statement.csv'),
            'statement_start': '2024-01-01',
            'statement_end':   '2024-01-31',
        },
        content_type='multipart/form-data',
        follow_redirects=False,
    )
    assert resp.status_code == 302, "Expected redirect to review after upload"
    batch_id = resp.headers.get('Location', '').split('batch=')[-1]
    assert batch_id, "No batch_id in redirect"

    rows, _ = app_module.db.get_office_import_staging(batch_id)
    assert rows, "No staged rows"

    # Build approve form keeping all rows
    form_data = {'batch_id': batch_id}
    for row in rows:
        rid = str(row['id'])
        form_data[f'keep_{rid}'] = 'on'
        form_data[f'ref_{rid}'] = row.get('description') or 'Import'
        form_data[f'source_{rid}'] = 'Bank Transfer'

    client.post('/office-account/import-approve', data=form_data, follow_redirects=True)

    # Fetch the Office Account page and parse display
    resp2 = client.get('/office-account')
    assert resp2.status_code == 200
    html = resp2.get_data(as_text=True)

    # Every Payment must appear as a negative amount in the HTML
    payment_descs = [
        'Office Rent', 'Microsoft 365', 'Internet Bill',
        'Monthly Staff Salaries', 'Professional Indemnity', 'Software Licence',
    ]
    receipt_descs = ['Client Fee Receipt', 'Consulting Income', 'Client Settlement']

    for desc in payment_descs:
        # The row containing this description must have a '-£' somewhere in its vicinity
        idx = html.find(desc)
        assert idx != -1, f"Description '{desc}' not found in page"
        # Grab a window around the description to check sign
        window = html[max(0, idx - 200): idx + 200]
        assert '-£' in window or 'amount-negative' in window, (
            f"Payment '{desc}' should display as negative, but no '-£' near it. "
            f"Window: {window[:300]}"
        )

    for desc in receipt_descs:
        idx = html.find(desc)
        if idx == -1:
            continue  # row may have been skipped (e.g. zero-amount opening balance)
        window = html[max(0, idx - 200): idx + 200]
        assert '+£' in window or 'amount-positive' in window, (
            f"Receipt '{desc}' should display as positive. Window: {window[:300]}"
        )


def test_import_balance_mathematically_correct(client):
    """
    After importing the full test CSV, the Office Account running balance must be
    consistent: receipts increase it, payments decrease it.

    Starting from an arbitrary baseline, importing:
      Receipts: +3500, +1850, +4200  = +9550
      Payments: -2200, -49.99, -89.50, -8500, -186.75, -950 = -11976.24

    Net change: +9550 - 11976.24 = -2426.24

    The post-import balance must equal pre-import balance - 2426.24.
    """
    balance_before = app_module.db.get_office_balance()

    resp = client.post(
        '/office-account/import-statement',
        data={
            'statement_file': (io.BytesIO(_full_csv()), 'statement.csv'),
            'statement_start': '2024-01-01',
            'statement_end':   '2024-01-31',
        },
        content_type='multipart/form-data',
        follow_redirects=False,
    )
    batch_id = resp.headers.get('Location', '').split('batch=')[-1]
    rows, _ = app_module.db.get_office_import_staging(batch_id)

    form_data = {'batch_id': batch_id}
    for row in rows:
        rid = str(row['id'])
        form_data[f'keep_{rid}'] = 'on'
        form_data[f'ref_{rid}'] = row.get('description') or 'Import'
        form_data[f'source_{rid}'] = 'Bank Transfer'

    client.post('/office-account/import-approve', data=form_data, follow_redirects=True)

    balance_after = app_module.db.get_office_balance()
    net_receipts = Decimal('3500.00') + Decimal('1850.00') + Decimal('4200.00')
    net_payments = (
        Decimal('2200.00') + Decimal('49.99') + Decimal('89.50') +
        Decimal('8500.00') + Decimal('186.75') + Decimal('950.00')
    )
    expected_delta = net_receipts - net_payments  # -2426.24
    actual_delta = balance_after - balance_before
    assert actual_delta == expected_delta, (
        f"Balance delta wrong. Expected {expected_delta}, got {actual_delta}. "
        f"Before={balance_before}, after={balance_after}"
    )


def test_payment_displays_negative_receipt_displays_positive(client):
    """
    Unit-level check: a single imported Payment row is stored with
    positive amount + type=Payment, and the office account page renders it
    as a negative amount (not positive).
    """
    csv_data = (
        b"Date,Description,Money Out,Money In\r\n"
        b"2024-02-01,Test Payment,500.00,\r\n"
        b"2024-02-02,Test Receipt,,750.00\r\n"
    )
    resp = client.post(
        '/office-account/import-statement',
        data={
            'statement_file': (io.BytesIO(csv_data), 'mini.csv'),
            'statement_start': '2024-02-01',
            'statement_end':   '2024-02-28',
        },
        content_type='multipart/form-data',
        follow_redirects=False,
    )
    batch_id = resp.headers.get('Location', '').split('batch=')[-1]
    rows, _ = app_module.db.get_office_import_staging(batch_id)

    # Verify staging values are stored as positive
    for row in rows:
        assert Decimal(str(row['amount'])) > 0, (
            f"Staged amount should always be positive; got {row['amount']} for {row['description']}"
        )

    form_data = {'batch_id': batch_id}
    for row in rows:
        rid = str(row['id'])
        form_data[f'keep_{rid}'] = 'on'
        form_data[f'ref_{rid}'] = row['description']
        form_data[f'source_{rid}'] = 'Bank Transfer'
    client.post('/office-account/import-approve', data=form_data, follow_redirects=True)

    html = client.get('/office-account').get_data(as_text=True)

    # Payment must render as negative
    idx = html.find('Test Payment')
    assert idx != -1
    window = html[max(0, idx - 300): idx + 300]
    assert 'amount-negative' in window, (
        f"Test Payment must have CSS class amount-negative. Window: {window}"
    )

    # Receipt must render as positive
    idx2 = html.find('Test Receipt')
    assert idx2 != -1
    window2 = html[max(0, idx2 - 300): idx2 + 300]
    assert 'amount-positive' in window2, (
        f"Test Receipt must have CSS class amount-positive. Window: {window2}"
    )


# ---------------------------------------------------------------------------
# Opening Balance Continuity Tests
# ---------------------------------------------------------------------------

def _csv_with_opening_balance(opening: str, transactions: str) -> bytes:
    """Build a test CSV with an explicit Opening Balance marker row."""
    header = "Date,Description,Money Out,Money In,Balance\r\n"
    ob_row = f"2024-11-01,Opening Balance,,,{opening}\r\n"
    return (header + ob_row + transactions).encode()


def _csv_no_opening_balance(transactions: str) -> bytes:
    """Build a test CSV with no opening balance marker and no balance column."""
    header = "Date,Description,Money Out,Money In\r\n"
    return (header + transactions.replace(',3500.00\r\n', '\r\n')).encode()


# --- Parser-level opening balance extraction ---

def test_parser_extracts_opening_balance_from_marker_row():
    """Parser must extract the opening balance from an 'Opening Balance' row."""
    csv_data = _csv_with_opening_balance(
        "25000.00",
        "2024-11-05,Client Fee Receipt,,3500.00,28500.00\r\n"
        "2024-11-10,Office Rent,2200.00,,26300.00\r\n",
    )
    result = parse_office_statement(csv_data, 'test.csv')
    assert result.error is None
    assert result.opening_balance == Decimal('25000.00'), (
        f"Expected opening_balance=25000.00, got {result.opening_balance}"
    )
    # Opening balance row must NOT appear in the transaction list
    assert len(result.rows) == 2
    descs = [r['description'] for r in result.rows]
    assert 'Opening Balance' not in descs


def test_parser_closing_balance_from_balance_column():
    """Parser must set closing_balance from the last row's balance column."""
    csv_data = _csv_with_opening_balance(
        "25000.00",
        "2024-11-05,Client Fee Receipt,,3500.00,28500.00\r\n"
        "2024-11-10,Office Rent,2200.00,,26300.00\r\n",
    )
    result = parse_office_statement(csv_data, 'test.csv')
    assert result.closing_balance == Decimal('26300.00'), (
        f"Expected closing_balance=26300.00, got {result.closing_balance}"
    )


def test_parser_no_opening_balance_returns_none():
    """When no opening balance row or balance column, opening_balance is None."""
    csv_data = b"Date,Description,Money Out,Money In\r\n2024-11-05,Office Rent,500.00,\r\n"
    result = parse_office_statement(csv_data, 'test.csv')
    assert result.error is None
    assert result.opening_balance is None


# --- Route-level balance continuity checks ---

def _upload_csv(client, csv_data: bytes, start: str, end: str):
    """Helper: POST a CSV to the import-statement endpoint and return batch_id."""
    resp = client.post(
        '/office-account/import-statement',
        data={
            'statement_file': (io.BytesIO(csv_data), 'stmt.csv'),
            'statement_start': start,
            'statement_end': end,
        },
        content_type='multipart/form-data',
        follow_redirects=False,
    )
    assert resp.status_code == 302, f"Expected redirect, got {resp.status_code}"
    batch_id = resp.headers.get('Location', '').split('batch=')[-1]
    assert batch_id, "No batch_id in redirect"
    return batch_id


def _approve_all(client, batch_id: str, confirm_mismatch: bool = False):
    """Helper: approve all staged rows for a batch."""
    rows, _ = app_module.db.get_office_import_staging(batch_id)
    form_data = {'batch_id': batch_id}
    for row in rows:
        rid = str(row['id'])
        form_data[f'keep_{rid}'] = 'on'
        form_data[f'ref_{rid}'] = row.get('description') or 'Import'
        form_data[f'source_{rid}'] = 'Bank Transfer'
    if confirm_mismatch:
        form_data['confirm_balance_mismatch'] = 'confirmed'
    return client.post(
        '/office-account/import-approve', data=form_data, follow_redirects=True
    )


def test_first_import_no_warning_shown(client):
    """
    A CSV without an opening-balance marker row and no balance column should result
    in 'no_opening_balance' status — no balance warning shown on the review page.
    """
    # No Balance column = parser cannot infer opening_balance → None → no_opening_balance
    csv_data = (
        b"Date,Description,Money Out,Money In\r\n"
        b"2099-01-05,Client Fee Receipt,,3500.00\r\n"
    )
    batch_id = _upload_csv(client, csv_data, '2099-01-01', '2099-01-31')
    rows, meta = app_module.db.get_office_import_staging(batch_id)

    assert meta is not None
    assert meta.get('balance_match') == 'no_opening_balance', (
        f"Expected 'no_opening_balance', got '{meta.get('balance_match')}'"
    )

    review = client.get(f'/office-account/import-review?batch={batch_id}')
    html = review.get_data(as_text=True)
    assert 'Opening Balance Mismatch' not in html
    assert 'Opening Balance matches' not in html


def test_matching_opening_balance_shows_success_banner(client):
    """
    When the CSV opening balance equals the previous statement's recorded closing
    balance (from office_statement_history), the review page must show the green
    match banner.
    The comparison must use statement history, NOT the running ledger balance.
    """
    # Step 1: establish a prior statement in history at a unique date range
    setup_csv = (
        b"Date,Description,Money Out,Money In,Balance\r\n"
        b"2098-01-01,Opening Balance,,,0.00\r\n"
        b"2098-01-10,Client Fee Receipt,,15000.00,15000.00\r\n"
        b"2098-01-20,Office Rent,2000.00,,13000.00\r\n"
    )
    b1 = _upload_csv(client, setup_csv, '2098-01-01', '2098-01-31')
    _approve_all(client, b1, confirm_mismatch=True)

    # Verify the statement history was recorded
    prev_closing = app_module.db.get_previous_statement_closing('2098-02-01')
    assert prev_closing is not None, "Statement history must be recorded after approval"
    assert prev_closing == Decimal('13000.00'), (
        f"Expected prev closing = 13000, got {prev_closing}"
    )

    # Step 2: upload February with opening = January closing
    feb_csv = (
        f"Date,Description,Money Out,Money In,Balance\r\n"
        f"2098-02-01,Opening Balance,,,{prev_closing:.2f}\r\n"
        f"2098-02-10,Client Fee Receipt,,5000.00,18000.00\r\n"
    ).encode()
    b2 = _upload_csv(client, feb_csv, '2098-02-01', '2098-02-28')
    rows2, meta2 = app_module.db.get_office_import_staging(b2)

    assert meta2 is not None
    assert meta2.get('balance_match') == 'match', (
        f"Expected 'match', got '{meta2.get('balance_match')}' "
        f"(opening={meta2.get('opening_balance')}, "
        f"prev_closing={meta2.get('ledger_balance_before')})"
    )

    review = client.get(f'/office-account/import-review?batch={b2}')
    html = review.get_data(as_text=True)
    assert 'Opening Balance Continuity Confirmed' in html or 'Opening Balance matches' in html or 'Opening balances match' in html or '✅' in html


def test_mismatched_opening_balance_shows_warning(client):
    """
    When the CSV opening balance does NOT match the previous statement's closing
    balance, the review page must show the red mismatch warning.
    The comparison is always previous_statement.closing_balance vs csv.opening_balance.
    """
    # Establish prior statement history with a known closing balance
    setup_csv = (
        b"Date,Description,Money Out,Money In,Balance\r\n"
        b"2098-03-01,Opening Balance,,,0.00\r\n"
        b"2098-03-10,Client Fee Receipt,,25000.00,25000.00\r\n"
    )
    b1 = _upload_csv(client, setup_csv, '2098-03-01', '2098-03-31')
    _approve_all(client, b1, confirm_mismatch=True)

    prev_closing = app_module.db.get_previous_statement_closing('2098-04-01')
    assert prev_closing == Decimal('25000.00')

    # Upload April with deliberately wrong opening (18000 instead of 25000)
    wrong_opening = Decimal('18000.00')
    apr_csv = (
        f"Date,Description,Money Out,Money In,Balance\r\n"
        f"2098-04-01,Opening Balance,,,{wrong_opening:.2f}\r\n"
        f"2098-04-10,Client Fee Receipt,,3500.00,21500.00\r\n"
    ).encode()
    b2 = _upload_csv(client, apr_csv, '2098-04-01', '2098-04-30')
    _, meta2 = app_module.db.get_office_import_staging(b2)

    assert meta2.get('balance_match') == 'mismatch', (
        f"Expected 'mismatch', got '{meta2.get('balance_match')}'"
    )

    review = client.get(f'/office-account/import-review?batch={b2}')
    html = review.get_data(as_text=True)
    assert 'Opening Balance Mismatch' in html
    assert '18,000.00' in html   # wrong opening shown
    assert '25,000.00' in html   # prev statement closing shown


def test_mismatch_approve_blocked_without_confirmation(client):
    """
    Approve must be blocked when the opening balance is mismatched and the user
    has NOT ticked the confirmation checkbox.
    """
    # Establish prior history
    setup_csv = (
        b"Date,Description,Money Out,Money In,Balance\r\n"
        b"2098-05-01,Opening Balance,,,0.00\r\n"
        b"2098-05-10,Receipt,,25000.00,25000.00\r\n"
    )
    b1 = _upload_csv(client, setup_csv, '2098-05-01', '2098-05-31')
    _approve_all(client, b1, confirm_mismatch=True)

    wrong_csv = (
        b"Date,Description,Money Out,Money In,Balance\r\n"
        b"2098-06-01,Opening Balance,,,99000.00\r\n"
        b"2098-06-05,Receipt,,3500.00,102500.00\r\n"
    )
    b2 = _upload_csv(client, wrong_csv, '2098-06-01', '2098-06-30')

    # Attempt approve WITHOUT confirmation
    resp = _approve_all(client, b2, confirm_mismatch=False)
    html = resp.get_data(as_text=True)
    assert (
        'balance' in html.lower() and
        ('mismatch' in html.lower() or 'does not match' in html.lower())
    ), "Expected mismatch error or review page, got: " + html[:500]


def test_mismatch_approve_succeeds_with_confirmation(client):
    """
    When the user ticks the confirmation checkbox, import succeeds despite mismatch.
    """
    setup_csv = (
        b"Date,Description,Money Out,Money In,Balance\r\n"
        b"2098-07-01,Opening Balance,,,0.00\r\n"
        b"2098-07-10,Receipt,,25000.00,25000.00\r\n"
    )
    b1 = _upload_csv(client, setup_csv, '2098-07-01', '2098-07-31')
    _approve_all(client, b1, confirm_mismatch=True)

    receipt_amount = Decimal('3500.00')
    wrong_csv = (
        f"Date,Description,Money Out,Money In,Balance\r\n"
        f"2098-08-01,Opening Balance,,,99000.00\r\n"
        f"2098-08-05,Receipt,,{receipt_amount:.2f},102500.00\r\n"
    ).encode()
    b2 = _upload_csv(client, wrong_csv, '2098-08-01', '2098-08-31')
    balance_before = app_module.db.get_office_balance()

    resp = _approve_all(client, b2, confirm_mismatch=True)
    assert resp.status_code == 200

    balance_after = app_module.db.get_office_balance()
    assert balance_after == balance_before + receipt_amount, (
        f"Balance should increase by {receipt_amount}. Before={balance_before}, after={balance_after}"
    )


def test_sequential_monthly_imports_balance_continuity(client):
    """
    Three consecutive imports must each show 'match' using statement history,
    not the running ledger balance.  Closing balance must carry forward correctly.
    """
    # All dates in 2099-09 through 2099-11 to avoid collisions with other tests.
    # First month: first_import (no prior history at this date range)
    # We use opening=0 so no BF transaction is needed.
    nov_receipt = Decimal('25000.00')
    nov_payment = Decimal('2000.00')
    nov_close = nov_receipt - nov_payment  # 23000

    nov_csv = (
        b"Date,Description,Money Out,Money In,Balance\r\n"
        b"2099-09-01,Opening Balance,,,0.00\r\n"
        b"2099-09-05,Client Fee Receipt,,25000.00,25000.00\r\n"
        b"2099-09-15,Office Rent,2000.00,,23000.00\r\n"
    )
    batch_nov = _upload_csv(client, nov_csv, '2099-09-01', '2099-09-30')
    _approve_all(client, batch_nov, confirm_mismatch=True)

    # Statement history must record closing = 23000
    hist_nov = app_module.db.get_previous_statement_closing('2099-10-01')
    assert hist_nov == Decimal('23000.00'), f"Nov closing in history: {hist_nov}"

    # December: opening must equal November closing from statement history
    dec_receipt = Decimal('5000.00')
    dec_close = hist_nov + dec_receipt  # 28000

    dec_csv = (
        f"Date,Description,Money Out,Money In,Balance\r\n"
        f"2099-10-01,Opening Balance,,,{hist_nov:.2f}\r\n"
        f"2099-10-10,Client Fee Receipt,,{dec_receipt:.2f},{dec_close:.2f}\r\n"
    ).encode()
    batch_dec = _upload_csv(client, dec_csv, '2099-10-01', '2099-10-31')
    _, dec_meta = app_module.db.get_office_import_staging(batch_dec)
    assert dec_meta.get('balance_match') == 'match', (
        f"October opening should match September closing. Meta: {dec_meta}"
    )
    _approve_all(client, batch_dec)

    hist_dec = app_module.db.get_previous_statement_closing('2099-11-01')
    assert hist_dec == Decimal('28000.00'), f"Dec closing in history: {hist_dec}"

    # January: opening must equal December closing
    jan_payment = Decimal('1500.00')
    jan_close = hist_dec - jan_payment  # 26500

    jan_csv = (
        f"Date,Description,Money Out,Money In,Balance\r\n"
        f"2099-11-01,Opening Balance,,,{hist_dec:.2f}\r\n"
        f"2099-11-20,Software Licence,{jan_payment:.2f},,{jan_close:.2f}\r\n"
    ).encode()
    batch_jan = _upload_csv(client, jan_csv, '2099-11-01', '2099-11-30')
    _, jan_meta = app_module.db.get_office_import_staging(batch_jan)
    assert jan_meta.get('balance_match') == 'match', (
        f"November opening should match October closing. Meta: {jan_meta}"
    )
    _approve_all(client, batch_jan)

    hist_jan = app_module.db.get_previous_statement_closing('2099-12-01')
    assert hist_jan == Decimal('26500.00'), f"Jan closing in history: {hist_jan}"


def test_statement_history_stores_closing_balance_after_approval(client):
    """
    After approving an import, office_statement_history must contain an entry
    with the correct opening_balance and closing_balance.
    """
    csv_data = (
        b"Date,Description,Money Out,Money In,Balance\r\n"
        b"2097-06-01,Opening Balance,,,10000.00\r\n"
        b"2097-06-05,Receipt,,4000.00,14000.00\r\n"
        b"2097-06-15,Payment,1500.00,,12500.00\r\n"
    )
    batch_id = _upload_csv(client, csv_data, '2097-06-01', '2097-06-30')
    _approve_all(client, batch_id)

    # Verify statement history
    closing = app_module.db.get_previous_statement_closing('2097-07-01')
    assert closing == Decimal('12500.00'), (
        f"Statement history closing balance: expected 12500, got {closing}"
    )

    # Verify the history is used (not the running ledger) for a subsequent import
    next_csv = (
        f"Date,Description,Money Out,Money In,Balance\r\n"
        f"2097-07-01,Opening Balance,,,12500.00\r\n"
        f"2097-07-10,Receipt,,5000.00,17500.00\r\n"
    ).encode()
    b2 = _upload_csv(client, next_csv, '2097-07-01', '2097-07-31')
    _, meta2 = app_module.db.get_office_import_staging(b2)
    assert meta2.get('balance_match') == 'match', (
        f"Expected match using statement history. balance_match={meta2.get('balance_match')}, "
        f"prev_closing={meta2.get('ledger_balance_before')}"
    )


def test_continuity_never_uses_running_ledger_balance(client):
    """
    CORE REGRESSION TEST: The continuity check must use statement history only.

    Scenario mirroring the reported bug:
      January:  Opening £25,000, Receipts £9,525, Payments £5,400 → Closing £29,125
      February: Opening £29,125 → must show MATCH (not compare against £4,125 net movement)

    £4,125 = net movement (9,525 - 5,400). The old buggy code returned this
    because it computed the running ledger sum after subtracting the BF transaction
    opening. The new code uses office_statement_history.closing_balance = 29,125.
    """
    # Use unique dates to avoid collision with other tests
    jan_start = '2097-01-01'
    jan_end = '2097-01-31'
    feb_start = '2097-02-01'
    feb_end = '2097-02-28'

    # January: first import, opening £25,000
    jan_csv = (
        b"Date,Description,Money Out,Money In,Balance\r\n"
        b"2097-01-01,Opening Balance,,,25000.00\r\n"
        b"2097-01-05,Client Fee,,9525.00,34525.00\r\n"
        b"2097-01-15,Office Expenses,5400.00,,29125.00\r\n"
    )
    b_jan = _upload_csv(client, jan_csv, jan_start, jan_end)
    _, meta_jan = app_module.db.get_office_import_staging(b_jan)
    assert meta_jan.get('balance_match') == 'first_import', (
        f"January must be first_import, got {meta_jan.get('balance_match')}"
    )
    _approve_all(client, b_jan)

    # Verify statement history records closing = 29125 (NOT 4125)
    jan_closing = app_module.db.get_previous_statement_closing(feb_start)
    assert jan_closing == Decimal('29125.00'), (
        f"Statement history must record January closing as 29125.00, got {jan_closing}. "
        f"If this returns 4125.00, the bug is that net movement is being used instead."
    )

    # February: opening £29,125 — must MATCH
    feb_csv = (
        b"Date,Description,Money Out,Money In,Balance\r\n"
        b"2097-02-01,Opening Balance,,,29125.00\r\n"
        b"2097-02-10,Client Fee,,8000.00,37125.00\r\n"
    )
    b_feb = _upload_csv(client, feb_csv, feb_start, feb_end)
    _, meta_feb = app_module.db.get_office_import_staging(b_feb)

    assert meta_feb.get('balance_match') == 'match', (
        f"February must show MATCH. Got '{meta_feb.get('balance_match')}'. "
        f"prev_closing stored={meta_feb.get('ledger_balance_before')}. "
        f"If mismatch, the code compared against net movement (4125) instead of "
        f"statement closing (29125)."
    )

    review = client.get(f'/office-account/import-review?batch={b_feb}')
    html = review.get_data(as_text=True)
    assert 'Opening Balance Mismatch' not in html, (
        "February review page must NOT show mismatch warning"
    )
    assert '29,125.00' in html, (
        "Review page must display the previous statement closing (£29,125.00)"
    )
