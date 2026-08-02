"""Tests for the Office Account VAT module."""
import os
from decimal import Decimal
from uuid import uuid4

import pytest

import app as app_module
from app import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _reset_vat_test_db():
    conn = app_module.db.get_connection()
    try:
        conn.execute("DELETE FROM office_statement_history")
        conn.execute("DELETE FROM vat_returns")
        conn.execute("DELETE FROM vat_description_rules")
        conn.execute("DELETE FROM vat_settings")
        conn.execute("DELETE FROM office_cashbook")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def reset_vat_db():
    _reset_vat_test_db()
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


def _login_admin(client):
    admin = app_module.db.get_user_by_username("admin")
    with client.session_transaction() as sess:
        sess["user_id"] = admin["user_id"] if admin else 1
        sess["username"] = "admin"
        sess["role"] = "admin"
        sess["sso_login"] = True
        sess["dev_mode_login"] = True


def test_vat_split_1200_gross():
    from lib.vat import split_vat_gross

    gross, net, vat = split_vat_gross(Decimal("1200"))
    assert gross == Decimal("1200.00")
    assert net == Decimal("1000.00")
    assert vat == Decimal("200.00")


def test_quarter_for_mar_jun_sep_dec_cycle():
    from datetime import date
    from lib.vat import quarter_for_date

    q = quarter_for_date(date(2026, 5, 15), "mar_jun_sep_dec")
    assert q["quarter_end"] == "2026-06-30"
    assert q["quarter_start"] == "2026-04-01"


def test_quarter_december_wraps_feb_may_aug_nov_cycle():
    from datetime import date
    from lib.vat import quarter_for_date, quarter_ordinal_label, quarter_period_label

    q = quarter_for_date(date(2026, 12, 2), "feb_may_aug_nov")
    assert q["quarter_end"] == "2027-02-28"
    assert q["quarter_start"] == "2026-12-01"
    assert quarter_ordinal_label(q, "feb_may_aug_nov") == "Q1 2027"

    q4 = quarter_for_date(date(2026, 12, 15), "mar_jun_sep_dec")
    assert quarter_ordinal_label(q4, "mar_jun_sep_dec") == "Q4 2026"
    assert quarter_period_label(q4) == "Oct–Dec 2026"


def test_hmrc_boxes_from_transactions():
    from lib.vat import calculate_hmrc_boxes

    txns = [
        {
            "transaction_type": "Receipt",
            "status": "Cleared",
            "vat_applicable": 1,
            "amount": "1200",
            "gross_amount": "1200",
            "net_amount": "1000",
            "vat_amount": "200",
            "is_vat_excluded": 0,
            "is_deleted": 0,
        },
        {
            "transaction_type": "Payment",
            "status": "Cleared",
            "vat_applicable": 1,
            "amount": "240",
            "gross_amount": "240",
            "net_amount": "200",
            "vat_amount": "40",
            "is_vat_excluded": 0,
            "is_deleted": 0,
        },
    ]
    boxes = calculate_hmrc_boxes(txns)
    assert boxes["box1"] == Decimal("200.00")
    assert boxes["box4"] == Decimal("40.00")
    assert boxes["box5"] == Decimal("160.00")
    assert boxes["box6"] == Decimal("1000.00")
    assert boxes["box7"] == Decimal("200.00")
    assert boxes["box2"] == Decimal("0")
    assert boxes["box8"] == Decimal("0")
    assert boxes["box9"] == Decimal("0")


def test_vat_auto_tag_rule(client):
    db = app_module.db
    db.upsert_vat_description_rule("Office Supplies Ltd", True, "admin")
    assert db.get_vat_description_rule("office supplies ltd") is True
    assert db.get_vat_description_rule("Unknown") is None


def test_vat_user_figures_follows_calculated_unless_overridden():
    import app as app_module
    from decimal import Decimal

    calculated = {f"box{i}": Decimal("100.00") for i in range(1, 10)}
    calculated["box2"] = Decimal("0")
    calculated["box3"] = Decimal("100.00")

    # No draft — all figures match calculated
    figures = app_module._vat_user_figures(None, calculated)
    assert figures["box1"] == Decimal("100.00")

    # Draft with no manual override — tracks fresh calculated after new imports
    draft = {f"box{i}": "50.00" for i in range(1, 10)}
    draft.update({f"calculated_box{i}": "50.00" for i in range(1, 10)})
    figures = app_module._vat_user_figures(draft, calculated)
    assert figures["box1"] == Decimal("100.00")

    # Manual override on box1 preserved
    draft["box1"] = "75.00"
    draft["calculated_box1"] = "50.00"
    figures = app_module._vat_user_figures(draft, calculated)
    assert figures["box1"] == Decimal("75.00")
    assert figures["box2"] == Decimal("0")


def test_hmrc_boxes_recalculates_null_vat_split():
    from lib.vat import calculate_hmrc_boxes

    txns = [{
        "transaction_type": "Receipt",
        "status": "Cleared",
        "vat_applicable": 1,
        "amount": "1200.00",
        "gross_amount": None,
        "net_amount": None,
        "vat_amount": None,
        "is_vat_excluded": 0,
        "is_deleted": 0,
    }]
    boxes = calculate_hmrc_boxes(txns)
    assert boxes["box1"] == Decimal("200.00")
    assert boxes["box6"] == Decimal("1000.00")


def test_vat_return_corrupt_cycle_redirects(client):
    _login_admin(client)
    conn = app_module.db.get_connection()
    conn.execute(
        "INSERT INTO vat_settings (id, activated, quarter_cycle) VALUES (1, 1, NULL)"
    )
    conn.commit()
    conn.close()

    resp = client.get("/office-account/vat/return", follow_redirects=False)
    assert resp.status_code == 302
    assert "vat/setup" in resp.location


def _insert_vat_receipt(db, txn_date: str, amount: str = "1200"):
    conn = db.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO office_cashbook (
                transaction_id, transaction_date, amount, transaction_type,
                reference, source, status, created_by,
                vat_applicable, gross_amount, net_amount, vat_amount
            ) VALUES (?, ?, ?, 'Receipt', 'REF', 'Bank Transfer', 'Cleared', 'admin', 1, ?, ?, ?)
            """,
            (
                f"T-{txn_date}",
                txn_date,
                amount,
                amount,
                str(Decimal(amount) / Decimal("1.2")),
                str(Decimal(amount) - Decimal(amount) / Decimal("1.2")),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_period_start_excludes_pre_period_transactions():
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin", "2026-10-01")
    settings = db.get_vat_settings()
    _insert_vat_receipt(db, "2026-09-15")
    _insert_vat_receipt(db, "2026-11-15")

    _, boxes_q3, _, _, txns_q3 = app_module._vat_quarter_context(
        "mar_jun_sep_dec", "2026-09-30", vat_settings=settings
    )
    assert len(txns_q3) == 0
    assert boxes_q3["box1"] == Decimal("0.00")

    _, boxes_q4, _, _, txns_q4 = app_module._vat_quarter_context(
        "mar_jun_sep_dec", "2026-12-31", vat_settings=settings
    )
    assert len(txns_q4) == 1
    assert boxes_q4["box1"] == Decimal("200.00")


def test_period_start_blank_uses_auto_quarter():
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin", None)
    settings = db.get_vat_settings()
    _insert_vat_receipt(db, "2026-09-15")

    _, boxes, _, _, txns = app_module._vat_quarter_context(
        "mar_jun_sep_dec", "2026-09-30", vat_settings=settings
    )
    assert len(txns) == 1
    assert boxes["box1"] == Decimal("200.00")


def test_deactivate_vat(client):
    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    db.deactivate_vat()
    settings = db.get_vat_settings()
    assert settings["activated"] == 0
    assert settings["quarter_cycle"] is None


def test_cycle_change_blocked_with_open_vat_transactions(client):
    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    from lib.vat import current_quarter

    q = current_quarter("mar_jun_sep_dec")
    _insert_vat_receipt(db, q["quarter_start"])

    resp = client.post(
        "/office-account/vat/setup?reconfigure=1",
        data={"quarter_cycle": "jan_apr_jul_oct", "reconfigure": "1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"unsubmitted VAT transactions" in resp.data
    assert db.get_vat_settings()["quarter_cycle"] == "mar_jun_sep_dec"


def test_vat_setup_and_return_routes(client):
    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")

    resp = client.get("/office-account/vat/return")
    assert resp.status_code == 200
    assert b"Box 1" in resp.data or b"box1" in resp.data.lower()


def test_import_approve_with_vat_split(client):
    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")

    batch_id = str(uuid4())
    rows = [{
        "date": "2026-05-10",
        "description": "Client fee payment",
        "reference": "REF001",
        "amount": Decimal("1200.00"),
        "transaction_type": "Receipt",
        "source": "Bank Transfer",
        "row_number": 1,
        "vat_applicable": False,
        "vat_auto_tagged": False,
        "is_desc_amount_duplicate": False,
    }]
    db.create_office_import_batch(
        batch_id, "test.csv", "2026-05-01", "2026-05-31", rows, "admin",
        opening_balance=Decimal("0"),
        closing_balance=Decimal("1200"),
        balance_match="first_import",
    )
    staged, _ = db.get_office_import_staging(batch_id)
    row_id = staged[0]["id"]

    resp = client.post(
        "/office-account/import-approve",
        data={
            "batch_id": batch_id,
            f"keep_{row_id}": "on",
            f"ref_{row_id}": "REF001",
            f"desc_{row_id}": "Client fee payment",
            f"amount_{row_id}": "1200.00",
            f"date_{row_id}": "2026-05-10",
            f"source_{row_id}": "Bank Transfer",
            f"cleared_present_{row_id}": "1",
            f"cleared_{row_id}": "on",
            f"vat_{row_id}": "on",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    oc_rows = db._get_office_cashbook_rows()
    vat_row = next(r for r in oc_rows if r.get("import_batch_id") == batch_id)
    assert vat_row["vat_applicable"] == 1
    assert Decimal(str(vat_row["net_amount"])) == Decimal("1000.00")
    assert Decimal(str(vat_row["vat_amount"])) == Decimal("200.00")
    assert db.get_vat_description_rule("Client fee payment") is True


def test_vat_return_submit_locks_quarter(client):
    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    from lib.vat import current_quarter

    quarter = current_quarter("mar_jun_sep_dec")
    db.save_vat_return_draft(
        quarter,
        {"box1": Decimal("0"), "box2": Decimal("0"), "box3": Decimal("0"),
         "box4": Decimal("0"), "box5": Decimal("0"), "box6": Decimal("0"),
         "box7": Decimal("0"), "box8": Decimal("0"), "box9": Decimal("0")},
        {"box1": Decimal("0"), "box2": Decimal("0"), "box3": Decimal("0"),
         "box4": Decimal("0"), "box5": Decimal("0"), "box6": Decimal("0"),
         "box7": Decimal("0"), "box8": Decimal("0"), "box9": Decimal("0")},
        "admin",
    )

    resp = client.post(
        "/office-account/vat/return/submit",
        data={
            "quarter_key": quarter["quarter_key"],
            **{f"box{i}": "0.00" for i in range(1, 10)},
            "hmrc_reference": "TEST-REF-123",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    locked = db.get_vat_return(quarter["quarter_key"])
    assert locked["is_locked"] == 1
    assert locked["hmrc_reference"] == "TEST-REF-123"


def test_quarter_end_banner_shows_when_unsubmitted(client, monkeypatch):
    """Red banner appears when quarter has ended and return not submitted."""
    from datetime import date
    import lib.vat as vat_module

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2027, 1, 15)

    monkeypatch.setattr(vat_module, "date", FakeDate)

    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    _insert_vat_receipt(db, "2026-11-15")

    resp = client.get("/office-account")
    assert resp.status_code == 200
    assert b"VAT Return Due" in resp.data
    assert b"Your VAT quarter ended" in resp.data
    assert b"submit your VAT return before" in resp.data


def test_quarter_end_banner_hidden_after_submit(client, monkeypatch):
    """Banner disappears once the ended quarter is submitted."""
    from datetime import date
    import lib.vat as vat_module
    from lib.vat import quarter_for_date

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2027, 1, 15)

    monkeypatch.setattr(vat_module, "date", FakeDate)

    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    q4 = quarter_for_date(date(2026, 11, 15), "mar_jun_sep_dec")
    _insert_vat_receipt(db, "2026-11-15")

    client.post(
        "/office-account/vat/return/submit",
        data={
            "quarter_key": q4["quarter_key"],
            **{f"box{i}": "200.00" if i == 1 else "0.00" for i in range(1, 10)},
        },
        follow_redirects=True,
    )

    resp = client.get("/office-account")
    assert resp.status_code == 200
    assert b"VAT Return Due" not in resp.data


def test_clean_quarter_start_after_submit(client, monkeypatch):
    """New quarter summary resets to zero; previous quarter txns do not bleed in."""
    from datetime import date
    import lib.vat as vat_module
    from lib.vat import quarter_for_date

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2027, 1, 15)

    monkeypatch.setattr(vat_module, "date", FakeDate)

    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    q4 = quarter_for_date(date(2026, 11, 15), "mar_jun_sep_dec")
    _insert_vat_receipt(db, "2026-11-15")

    client.post(
        "/office-account/vat/return/submit",
        data={
            "quarter_key": q4["quarter_key"],
            **{f"box{i}": "200.00" if i == 1 else "0.00" for i in range(1, 10)},
        },
        follow_redirects=True,
    )

    resp = client.get("/office-account")
    assert resp.status_code == 200
    assert b"Jan" in resp.data or b"2027" in resp.data
    assert b"0.00" in resp.data

    cycle = db.get_vat_settings()["quarter_cycle"]
    open_key = app_module._resolve_vat_active_quarter(cycle)["quarter_key"]
    _, boxes, summary, _, txns = app_module._vat_quarter_context(
        cycle, open_key, vat_settings=db.get_vat_settings()
    )
    assert open_key == "2027-03-31"
    assert len(txns) == 0
    assert summary["output_vat"] == Decimal("0.00")
    assert boxes["box1"] == Decimal("0.00")


def test_vat_history_shows_submitted_boxes(client):
    """Submitted quarter history accordion includes all 9 HMRC box figures."""
    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    from lib.vat import current_quarter

    quarter = current_quarter("mar_jun_sep_dec")
    boxes = {f"box{i}": Decimal("0") for i in range(1, 10)}
    boxes["box1"] = Decimal("150.00")
    boxes["box5"] = Decimal("150.00")
    db.save_vat_return_draft(quarter, boxes, boxes, "admin")
    db.submit_vat_return(quarter["quarter_key"], boxes, "admin", "HMRC-999")

    resp = client.get(f"/office-account/vat/return?expand={quarter['quarter_key']}")
    assert resp.status_code == 200
    assert b"Quarter History" in resp.data
    assert b"Submitted figure" in resp.data
    assert b"150.00" in resp.data
    assert b"Submitted" in resp.data


def test_resolve_vat_display_quarter_uses_latest_transaction_date(monkeypatch):
    """Display quarter follows latest VAT txn date, not today's calendar quarter."""
    from datetime import date
    import lib.vat as vat_module
    from lib.vat import quarter_for_date, quarter_period_label

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 8, 15)

    monkeypatch.setattr(vat_module, "date", FakeDate)

    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    _insert_vat_receipt(db, "2026-11-15")

    quarter = app_module._resolve_vat_display_quarter("mar_jun_sep_dec")
    expected = quarter_for_date(date(2026, 11, 15), "mar_jun_sep_dec")
    assert quarter["quarter_key"] == expected["quarter_key"]
    assert "2026" in quarter_period_label(quarter)


def test_vat_summary_shows_figures_for_future_quarter(client, monkeypatch):
    """Office Account VAT summary includes future-dated approved VAT transactions."""
    from datetime import date
    import lib.vat as vat_module

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 8, 15)

    monkeypatch.setattr(vat_module, "date", FakeDate)

    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    _insert_vat_receipt(db, "2026-11-15")

    resp = client.get("/office-account")
    assert resp.status_code == 200
    assert b"2026" in resp.data
    assert b"200.00" in resp.data


def test_vat_quarter_key_saved_on_import_approve(client):
    """Approve saves vat_quarter_key matching the transaction date quarter."""
    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("mar_jun_sep_dec", "admin")
    from lib.vat import quarter_for_date
    from datetime import date

    batch_id = str(uuid4())
    rows = [{
        "date": "2026-11-20",
        "description": "VAT receipt",
        "reference": "REF-VAT",
        "amount": Decimal("1200.00"),
        "transaction_type": "Receipt",
        "source": "Bank Transfer",
        "row_number": 1,
        "vat_applicable": True,
        "vat_auto_tagged": False,
        "is_desc_amount_duplicate": False,
    }]
    db.create_office_import_batch(
        batch_id, "test.csv", "2026-11-01", "2026-11-30", rows, "admin",
        opening_balance=Decimal("0"),
        closing_balance=Decimal("1200"),
        balance_match="first_import",
    )
    staged, _ = db.get_office_import_staging(batch_id)
    row_id = staged[0]["id"]
    expected_key = quarter_for_date(
        date(2026, 11, 20), "mar_jun_sep_dec"
    )["quarter_key"]

    client.post(
        "/office-account/import-approve",
        data={
            "batch_id": batch_id,
            f"keep_{row_id}": "on",
            f"ref_{row_id}": "REF-VAT",
            f"desc_{row_id}": "VAT receipt",
            f"amount_{row_id}": "1200.00",
            f"date_{row_id}": "2026-11-20",
            f"source_{row_id}": "Bank Transfer",
            f"cleared_present_{row_id}": "1",
            f"cleared_{row_id}": "on",
            f"vat_{row_id}": "on",
        },
        follow_redirects=True,
    )

    oc_rows = db._get_office_cashbook_rows()
    vat_row = next(r for r in oc_rows if r.get("import_batch_id") == batch_id)
    assert vat_row["vat_applicable"] == 1
    assert vat_row["vat_quarter_key"] == expected_key

    _, boxes, summary, _, _ = app_module._vat_quarter_context(
        "mar_jun_sep_dec", expected_key, vat_settings=db.get_vat_settings()
    )
    assert summary["output_vat"] == Decimal("200.00")
    assert boxes["box1"] == Decimal("200.00")


def test_unsubmitted_prior_quarter_reminder(client):
    """Reminder shown when an older VAT quarter is open and a newer quarter has txns."""
    _login_admin(client)
    db = app_module.db
    db.save_vat_setup("feb_may_aug_nov", "admin")
    from lib.vat import quarter_for_date
    from datetime import date

    nov_key = quarter_for_date(date(2026, 11, 15), "feb_may_aug_nov")["quarter_key"]
    dec_key = quarter_for_date(date(2026, 12, 15), "feb_may_aug_nov")["quarter_key"]
    _insert_vat_receipt(db, "2026-11-15")
    _insert_vat_receipt(db, "2026-12-10")

    active = app_module._resolve_vat_active_quarter("feb_may_aug_nov")
    assert active["quarter_key"] == dec_key

    reminders = app_module._vat_unsubmitted_quarter_reminders(
        "feb_may_aug_nov", active["quarter_key"], db.get_vat_settings()
    )
    assert len(reminders) == 1
    assert reminders[0]["quarter_key"] == nov_key
    assert b"unsubmitted VAT quarter" in reminders[0]["message"].encode()

    resp = client.get("/office-account")
    assert resp.status_code == 200
    assert b"Unsubmitted VAT Quarter" in resp.data
    assert b"unsubmitted VAT quarter" in resp.data

    # December summary only — November does not bleed in
    _, _, dec_summary, _, dec_txns = app_module._vat_quarter_context(
        "feb_may_aug_nov", dec_key, vat_settings=db.get_vat_settings()
    )
    assert len(dec_txns) == 1
    assert dec_txns[0]["transaction_date"] == "2026-12-10"
    assert dec_summary["output_vat"] == Decimal("200.00")

    # Submit November — reminder clears, December still active
    client.post(
        "/office-account/vat/return/submit",
        data={
            "quarter_key": nov_key,
            **{f"box{i}": "200.00" if i == 1 else "0.00" for i in range(1, 10)},
        },
        follow_redirects=True,
    )
    reminders_after = app_module._vat_unsubmitted_quarter_reminders(
        "feb_may_aug_nov",
        app_module._resolve_vat_active_quarter("feb_may_aug_nov")["quarter_key"],
        db.get_vat_settings(),
    )
    assert reminders_after == []
    resp2 = client.get("/office-account")
    assert b"Unsubmitted VAT Quarter" not in resp2.data
