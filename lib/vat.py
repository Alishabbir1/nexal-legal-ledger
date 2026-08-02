"""
VAT module — quarter calculations, 20% split, and HMRC box aggregation.

Fixed VAT rate: 20%. Quarter cycles are one-time firm configuration.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

VAT_RATE = Decimal('0.20')
VAT_DIVISOR = Decimal('1.20')

QUARTER_CYCLES = {
    'mar_jun_sep_dec': {
        'label': 'March / June / September / December',
        'months': (3, 6, 9, 12),
    },
    'jan_apr_jul_oct': {
        'label': 'January / April / July / October',
        'months': (1, 4, 7, 10),
    },
    'feb_may_aug_nov': {
        'label': 'February / May / August / November',
        'months': (2, 5, 8, 11),
    },
}

VALID_CYCLES = tuple(QUARTER_CYCLES.keys())


def normalize_description(description: str | None) -> str:
    """Normalize description for auto-tag lookup."""
    return (description or '').strip().lower()


def split_vat_gross(gross: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """
    Split gross amount into net + VAT at 20%.
    gross = net * 1.2  →  net = gross / 1.2, vat = gross - net
    """
    gross = gross.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    net = (gross / VAT_DIVISOR).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    vat = (gross - net).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return gross, net, vat


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_one_calendar_month(d: date) -> date:
    month = d.month + 1
    year = d.year
    if month > 12:
        month = 1
        year += 1
    day = min(d.day, _last_day_of_month(year, month))
    return date(year, month, day)


def submission_deadline(quarter_end: date) -> date:
    """HMRC: 1 calendar month and 7 days after quarter end."""
    return _add_one_calendar_month(quarter_end) + timedelta(days=7)


def _apply_period_start_override(
    quarter: dict[str, Any],
    period_start_override: date | None,
) -> dict[str, Any]:
    """Adjust quarter start when firm joined mid-period."""
    if not period_start_override:
        return quarter
    q_start = parse_date(quarter.get('quarter_start'))
    q_end = parse_date(quarter.get('quarter_end'))
    if not q_start or not q_end:
        return quarter
    if period_start_override > q_end:
        return quarter
    if period_start_override > q_start:
        quarter = dict(quarter)
        quarter['quarter_start'] = period_start_override.isoformat()
        quarter['label'] = (
            f"{period_start_override.strftime('%d %b %Y')} – "
            f"{q_end.strftime('%d %b %Y')}"
        )
    return quarter


def transaction_on_or_after_period_start(
    txn_date: str | None,
    period_start_override: date | None,
) -> bool:
    """False when transaction predates the firm's configured VAT period start."""
    if not period_start_override:
        return True
    d = parse_date(txn_date)
    return bool(d and d >= period_start_override)


def quarter_for_date(
    d: date,
    cycle_key: str,
    period_start_override: date | None = None,
) -> dict[str, Any]:
    """Return quarter metadata for a date within the firm's VAT cycle."""
    if cycle_key not in QUARTER_CYCLES:
        raise ValueError(f'Unknown VAT quarter cycle: {cycle_key}')
    end_months = QUARTER_CYCLES[cycle_key]['months']

    q_end_month = None
    q_end_year = d.year
    for m in end_months:
        if d.month <= m:
            q_end_month = m
            break
    if q_end_month is None:
        # e.g. December in feb/may/aug/nov cycle → quarter ending February next year
        q_end_month = end_months[0]
        q_end_year = d.year + 1

    q_end = date(q_end_year, q_end_month, _last_day_of_month(q_end_year, q_end_month))

    idx = end_months.index(q_end_month)
    if idx == 0:
        prev_end_month = end_months[-1]
        prev_end_year = q_end_year - 1
    else:
        prev_end_month = end_months[idx - 1]
        prev_end_year = q_end_year

    prev_end = date(
        prev_end_year,
        prev_end_month,
        _last_day_of_month(prev_end_year, prev_end_month),
    )
    q_start = prev_end + timedelta(days=1)
    quarter = _pack_quarter(q_start, q_end, cycle_key)
    return _apply_period_start_override(quarter, period_start_override)


def _pack_quarter(q_start: date, q_end: date, cycle_key: str) -> dict[str, Any]:
    deadline = submission_deadline(q_end)
    today = date.today()
    return {
        'quarter_key': q_end.isoformat(),
        'quarter_start': q_start.isoformat(),
        'quarter_end': q_end.isoformat(),
        'submission_deadline': deadline.isoformat(),
        'days_until_deadline': (deadline - today).days,
        'cycle_key': cycle_key,
        'label': f"{q_start.strftime('%d %b %Y')} – {q_end.strftime('%d %b %Y')}",
    }


def current_quarter(
    cycle_key: str,
    as_of: date | None = None,
    period_start_override: date | None = None,
) -> dict[str, Any]:
    return quarter_for_date(
        as_of or date.today(), cycle_key, period_start_override
    )


def parse_date(value: str | None) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def transaction_in_quarter(txn_date: str, quarter: dict[str, Any]) -> bool:
    d = parse_date(txn_date)
    if not d:
        return False
    start = parse_date(quarter['quarter_start'])
    end = parse_date(quarter['quarter_end'])
    return start <= d <= end if start and end else False


def quarter_period_label(quarter: dict[str, Any]) -> str:
    """Short period label e.g. Oct–Dec 2026."""
    start = parse_date(quarter.get('quarter_start'))
    end = parse_date(quarter.get('quarter_end'))
    if not start or not end:
        return quarter.get('label', '')
    sm, em = start.strftime('%b'), end.strftime('%b')
    if start.year == end.year:
        return f'{sm}–{em} {end.year}'
    return f'{sm} {start.year}–{em} {end.year}'


def quarter_ordinal_label(quarter: dict[str, Any], cycle_key: str) -> str:
    """Ordinal label e.g. Q4 2026 from quarter end within the firm's cycle."""
    end = parse_date(quarter.get('quarter_end'))
    if not end or cycle_key not in QUARTER_CYCLES:
        return quarter.get('quarter_key', '')
    months = QUARTER_CYCLES[cycle_key]['months']
    try:
        q_num = months.index(end.month) + 1
    except ValueError:
        return quarter.get('quarter_key', '')
    return f'Q{q_num} {end.year}'


def calculate_hmrc_boxes(transactions: list[dict[str, Any]]) -> dict[str, Decimal]:
    """
    Compute HMRC VAT return boxes from approved office_cashbook rows.
    Excluded rows (is_vat_excluded) are omitted. Only Cleared rows count.
    """
    output_vat = Decimal('0')
    input_vat = Decimal('0')
    sales_ex_vat = Decimal('0')
    purchases_ex_vat = Decimal('0')

    for txn in transactions:
        if txn.get('is_vat_excluded'):
            continue
        if (txn.get('status') or 'Cleared') != 'Cleared':
            continue
        if txn.get('is_deleted'):
            continue

        amount = Decimal(str(txn.get('amount') or '0'))
        vat_applicable = bool(txn.get('vat_applicable'))
        txn_type = txn.get('transaction_type')

        if vat_applicable:
            net = Decimal(str(txn.get('net_amount') or '0'))
            vat = Decimal(str(txn.get('vat_amount') or '0'))
            if vat == 0 and net == 0:
                gross = Decimal(str(txn.get('gross_amount') or txn.get('amount') or '0'))
                if gross > 0:
                    _, net, vat = split_vat_gross(gross)
        else:
            net = amount
            vat = Decimal('0')

        if txn_type == 'Receipt':
            sales_ex_vat += net
            if vat_applicable:
                output_vat += vat
        elif txn_type == 'Payment':
            purchases_ex_vat += net
            if vat_applicable:
                input_vat += vat

    box1 = output_vat.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    box2 = Decimal('0')
    box3 = box1 + box2
    box4 = input_vat.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    box5 = box3 - box4
    box6 = sales_ex_vat.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    box7 = purchases_ex_vat.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    box8 = Decimal('0')
    box9 = Decimal('0')

    return {
        'box1': box1,
        'box2': box2,
        'box3': box3,
        'box4': box4,
        'box5': box5,
        'box6': box6,
        'box7': box7,
        'box8': box8,
        'box9': box9,
    }


def quarter_summary_from_boxes(boxes: dict[str, Decimal]) -> dict[str, Decimal]:
    return {
        'output_vat': boxes['box1'],
        'input_vat': boxes['box4'],
        'net_owed': boxes['box5'],
    }
