"""Tests for office account transaction reversal."""
import os
from decimal import Decimal

import pytest

import app as app_module
from app import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _reset_office_db():
    conn = app_module.db.get_connection()
    try:
        for t in (
            'office_statement_history', 'office_import_staging',
            'vat_saved_months', 'vat_returns', 'vat_description_rules', 'vat_settings',
            'office_fee_transfers', 'office_cashbook', 'audit_log',
        ):
            conn.execute(f'DELETE FROM {t}')
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def reset_db():
    _reset_office_db()
    yield


@pytest.fixture()
def client(monkeypatch):
    from nexal_platform import session_security

    monkeypatch.setattr(
        session_security, "validate_sso_session_binding", lambda *a, **kw: None
    )
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c


def _login(client, role='admin'):
    user = app_module.db.get_user_by_username('admin' if role == 'admin' else 'staff')
    username = 'admin' if role == 'admin' else 'staff'
    if not user and role == 'staff':
        conn = app_module.db.get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, full_name) "
            "VALUES ('staff', 'x', 'staff', 'Staff User')"
        )
        conn.commit()
        conn.close()
        user = app_module.db.get_user_by_username('staff')
    with client.session_transaction() as sess:
        sess['user_id'] = user['user_id'] if user else 1
        sess['username'] = username
        sess['role'] = role
        sess['sso_login'] = True
        sess['dev_mode_login'] = True


def _insert_office_receipt(db, amount='2500', vat=False, quarter_key=None):
    conn = db.get_connection()
    try:
        if vat:
            gross = Decimal(amount)
            net = gross / Decimal('1.2')
            vat_amt = gross - net
            conn.execute(
                """
                INSERT INTO office_cashbook (
                    transaction_id, transaction_date, amount, transaction_type,
                    reference, source, status, created_by,
                    vat_applicable, gross_amount, net_amount, vat_amount, vat_quarter_key
                ) VALUES ('T-1', '2026-11-15', ?, 'Receipt', 'INV-1', 'Bank Transfer',
                    'Cleared', 'admin', 1, ?, ?, ?, ?)
                """,
                (str(gross), str(gross), str(net), str(vat_amt), quarter_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO office_cashbook (
                    transaction_id, transaction_date, amount, transaction_type,
                    reference, source, status, created_by
                ) VALUES ('T-1', '2026-11-15', ?, 'Receipt', 'INV-1', 'Bank Transfer',
                    'Cleared', 'admin')
                """,
                (amount,),
            )
        conn.commit()
        return conn.execute("SELECT id FROM office_cashbook ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def test_reverse_creates_opposite_entry_and_marks_original(client):
    _login(client)
    db = app_module.db
    row_id = _insert_office_receipt(db, '2500')
    assert db.get_office_balance() == Decimal('2500')

    resp = client.post(
        f'/office-account/reverse/{row_id}',
        data={'reversal_reason': 'Posted in error — duplicate invoice'},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b'reversed' in resp.data.lower()

    assert db.get_office_balance() == Decimal('0')
    conn = db.get_connection()
    orig = dict(conn.execute("SELECT * FROM office_cashbook WHERE id = ?", (row_id,)).fetchone())
    rev = dict(conn.execute(
        "SELECT * FROM office_cashbook WHERE reversal_of = ?", (row_id,)
    ).fetchone())
    conn.close()

    assert orig['reversal_status'] == 'REVERSED'
    assert orig['reversal_reason'] == 'Posted in error — duplicate invoice'
    assert rev['transaction_type'] == 'Payment'
    assert Decimal(str(rev['amount'])) == Decimal('2500')
    assert 'Reversal of' in (rev['description'] or '')


def test_reverse_vat_transaction_updates_boxes(client):
    _login(client)
    db = app_module.db
    db.save_vat_setup('mar_jun_sep_dec', 'admin')
    row_id = _insert_office_receipt(db, '1200', vat=True, quarter_key='2026-12-31')

    _, boxes_before, _, _, _ = app_module._vat_quarter_context(
        'mar_jun_sep_dec', '2026-12-31', vat_settings=db.get_vat_settings()
    )
    assert boxes_before['box1'] == Decimal('200.00')

    client.post(
        f'/office-account/reverse/{row_id}',
        data={'reversal_reason': 'Invoice cancelled by supplier'},
        follow_redirects=True,
    )

    _, boxes_after, _, _, _ = app_module._vat_quarter_context(
        'mar_jun_sep_dec', '2026-12-31', vat_settings=db.get_vat_settings()
    )
    assert boxes_after['box1'] == Decimal('0.00')


def test_cannot_reverse_submitted_vat_quarter(client):
    _login(client)
    db = app_module.db
    db.save_vat_setup('mar_jun_sep_dec', 'admin')
    row_id = _insert_office_receipt(db, '1200', vat=True, quarter_key='2026-12-31')
    boxes = {f'box{i}': Decimal('200.00') if i == 1 else Decimal('0') for i in range(1, 10)}
    db.submit_vat_return('2026-12-31', boxes, 'admin')

    resp = client.post(
        f'/office-account/reverse/{row_id}',
        data={'reversal_reason': 'Should be blocked by VAT lock'},
        follow_redirects=True,
    )
    assert b'submitted VAT quarter' in resp.data
    assert db.get_office_balance() == Decimal('1200')


def test_cannot_reverse_already_reversed_or_reversal_entry(client):
    _login(client)
    db = app_module.db
    row_id = _insert_office_receipt(db, '500')

    client.post(
        f'/office-account/reverse/{row_id}',
        data={'reversal_reason': 'First reversal reason here'},
        follow_redirects=True,
    )

    resp2 = client.post(
        f'/office-account/reverse/{row_id}',
        data={'reversal_reason': 'Attempt second reversal'},
        follow_redirects=True,
    )
    assert b'already been reversed' in resp2.data

    conn = db.get_connection()
    rev_id = conn.execute(
        "SELECT id FROM office_cashbook WHERE reversal_of = ?", (row_id,)
    ).fetchone()[0]
    conn.close()

    resp3 = client.post(
        f'/office-account/reverse/{rev_id}',
        data={'reversal_reason': 'Cannot reverse reversal'},
        follow_redirects=True,
    )
    assert b'reversal entry' in resp3.data.lower()


def test_reversal_reason_minimum_length(client):
    _login(client)
    db = app_module.db
    row_id = _insert_office_receipt(db, '100')

    resp = client.post(
        f'/office-account/reverse/{row_id}',
        data={'reversal_reason': 'bad'},
        follow_redirects=True,
    )
    assert b'minimum 5 characters' in resp.data
    assert db.get_office_balance() == Decimal('100')


def test_staff_cannot_reverse_office_transaction(client):
    _login(client, role='staff')
    db = app_module.db
    row_id = _insert_office_receipt(db, '100')

    resp = client.post(
        f'/office-account/reverse/{row_id}',
        data={'reversal_reason': 'Staff should not reverse'},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert db.get_office_balance() == Decimal('100')


def test_reversal_audit_log_and_report_fields(client):
    _login(client)
    db = app_module.db
    row_id = _insert_office_receipt(db, '750')

    client.post(
        f'/office-account/reverse/{row_id}',
        data={'reversal_reason': 'Duplicate bank deposit entry'},
        follow_redirects=True,
    )

    entries = db.get_audit_log_entries(limit=20)
    reversal_logs = [
        e for e in entries
        if e.get('action') == 'OFFICE_TRANSACTION_REVERSED'
        and e.get('record_id') == str(row_id)
    ]
    assert reversal_logs
    assert 'Duplicate bank deposit entry' in reversal_logs[0].get('details', '')

    txns = db.get_office_transactions()
    statuses = {t.get('office_cashbook_id'): app_module._office_reversal_report_fields(t)[0] for t in txns if t.get('office_cashbook_id')}
    assert statuses[row_id] == 'Reversed'
    rev_row = next(t for t in txns if t.get('reversal_of') == row_id)
    assert app_module._office_reversal_report_fields(rev_row)[0] == 'Reversal'
