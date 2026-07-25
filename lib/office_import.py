"""
Office Account bank statement import parser.

Phase 1: CSV import with flexible column matching for UK bank statement formats.
Architecture is designed so OFX / MT940 parsers can be dropped in without
touching any other module — just extend `parse_office_statement()` with new
extension handlers.

Each returned row dict:
    date            str  YYYY-MM-DD
    description     str
    reference       str  (falls back to first 50 chars of description)
    amount          Decimal  (always positive)
    transaction_type  str  'Receipt' or 'Payment'
    balance         Decimal or None  (running balance from statement, if present)
    row_number      int  (1-based source file row, for tracing)
"""

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Column-name normalisation helpers
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    """Lower-case, strip, collapse separators."""
    return re.sub(r'[\s\-_()/]+', '_', (name or '').strip().lower()).strip('_')


def _find_col(header_map: Dict[str, str], candidates: set) -> Optional[str]:
    """
    Find the first matching column.
    header_map: {normalised_name -> original_name}
    """
    for c in candidates:
        nc = _norm(c)
        if nc in header_map:
            return header_map[nc]
    return None


# ---------------------------------------------------------------------------
# Value parsers
# ---------------------------------------------------------------------------

def _parse_amount(val) -> Optional[Decimal]:
    """Parse currency value from any common representation."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # (1,234.56) → negative
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    # Strip currency symbols, commas, spaces
    s = re.sub(r'[£$€¥,\s]', '', s)
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(val) -> Optional[str]:
    """Parse date into YYYY-MM-DD from many UK-common formats."""
    if val is None:
        return None
    if hasattr(val, 'strftime'):
        return val.strftime('%Y-%m-%d')
    s = str(val).strip()
    if not s:
        return None
    # Try exact-length formats first
    for fmt in (
        '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
        '%Y/%m/%d', '%d/%m/%y', '%m/%d/%Y',
        '%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y',
    ):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    # Truncate to 10 chars for ISO-like strings with time suffix
    if len(s) > 10:
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                return datetime.strptime(s[:10], fmt).strftime('%Y-%m-%d')
            except ValueError:
                pass
    return None


# ---------------------------------------------------------------------------
# Column-name candidate sets (UK bank statement vocabulary)
# ---------------------------------------------------------------------------

DATE_COLS = {
    'date', 'transaction_date', 'trans_date', 'txn_date',
    'value_date', 'posted_date', 'booking_date', 'entry_date',
}

DEBIT_COLS = {
    'debit', 'debit_amount', 'debits',
    'money_out', 'paid_out', 'withdrawal', 'withdrawals',
    'out', 'dr',
}

CREDIT_COLS = {
    'credit', 'credit_amount', 'credits',
    'money_in', 'paid_in', 'deposit', 'deposits',
    'in', 'cr',
}

# Single-column amount (signed or unsigned with separate type indicator)
AMOUNT_COLS = {
    'amount', 'value', 'sum', 'total', 'net_amount',
    'transaction_amount', 'txn_amount',
}

DESC_COLS = {
    'description', 'narrative', 'details', 'particulars',
    'memo', 'payment_details', 'transaction_details', 'trans_description',
    'counterparty', 'counter_party', 'payee', 'beneficiary',
    'reference_narrative', 'remittance_info',
}

REF_COLS = {
    'reference', 'ref', 'transaction_ref', 'payment_reference',
    'cheque_number', 'cheque_no', 'pay_ref', 'payref',
}

BALANCE_COLS = {
    'balance', 'running_balance', 'account_balance', 'closing_balance',
    'balance_gbp',
}

# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

def parse_office_csv(content: bytes) -> Tuple[List[Dict], Optional[str]]:
    """
    Parse a bank-statement CSV file for Office Account import.

    Returns (rows, error_message).
    On success: rows is a non-empty list of dicts, error_message is None.
    On failure: rows is [], error_message describes the problem.
    """
    # Decode with BOM stripping
    try:
        text = content.decode('utf-8-sig', errors='replace')
    except Exception as e:
        return [], f"Could not decode file: {e}"

    # Parse CSV
    try:
        reader = csv.DictReader(io.StringIO(text))
        raw_rows = list(reader)
    except Exception as e:
        return [], f"Could not parse CSV structure: {e}"

    if not raw_rows:
        return [], "The file is empty or contains no data rows."

    # Build normalised header map
    h = {_norm(col): col for col in raw_rows[0].keys()}

    # Locate date column (mandatory)
    date_col = _find_col(h, DATE_COLS)
    if date_col is None:
        found = ', '.join(raw_rows[0].keys()) or '(none)'
        return [], (
            "Required column 'Date' not found. "
            f"Columns present: {found}. "
            "Expected names like: Date, Transaction Date, Value Date."
        )

    # Locate amount columns
    debit_col = _find_col(h, DEBIT_COLS)
    credit_col = _find_col(h, CREDIT_COLS)
    amount_col = _find_col(h, AMOUNT_COLS) if not (debit_col or credit_col) else None

    if not debit_col and not credit_col and not amount_col:
        found = ', '.join(raw_rows[0].keys()) or '(none)'
        return [], (
            "Required amount column not found. "
            f"Columns present: {found}. "
            "Expected names like: Amount, Debit, Credit, Money In, Money Out."
        )

    desc_col = _find_col(h, DESC_COLS)
    ref_col = _find_col(h, REF_COLS)
    bal_col = _find_col(h, BALANCE_COLS)

    rows: List[Dict] = []
    skipped = 0

    for i, raw in enumerate(raw_rows, start=2):  # start=2: row 1 is header
        date_val = _parse_date(raw.get(date_col, ''))
        if date_val is None:
            skipped += 1
            continue

        description = (raw.get(desc_col) or '').strip() if desc_col else ''
        reference = (raw.get(ref_col) or '').strip() if ref_col else ''
        balance = _parse_amount(raw.get(bal_col)) if bal_col else None

        # Fallback reference
        ref_fallback = reference or description[:50] or 'Import'

        if debit_col and credit_col:
            # Separate debit / credit columns
            debit_val = _parse_amount(raw.get(debit_col, ''))
            credit_val = _parse_amount(raw.get(credit_col, ''))
            if debit_val is not None and debit_val != Decimal('0'):
                rows.append(_make_row(date_val, description, ref_fallback,
                                      abs(debit_val), 'Payment', balance, i))
            if credit_val is not None and credit_val != Decimal('0'):
                rows.append(_make_row(date_val, description, ref_fallback,
                                      abs(credit_val), 'Receipt', balance, i))

        elif debit_col:
            amt = _parse_amount(raw.get(debit_col, ''))
            if amt is not None and amt != Decimal('0'):
                rows.append(_make_row(date_val, description, ref_fallback,
                                      abs(amt), 'Payment', balance, i))

        elif credit_col:
            amt = _parse_amount(raw.get(credit_col, ''))
            if amt is not None and amt != Decimal('0'):
                rows.append(_make_row(date_val, description, ref_fallback,
                                      abs(amt), 'Receipt', balance, i))

        else:
            # Single signed amount column
            amt = _parse_amount(raw.get(amount_col, ''))
            if amt is not None and amt != Decimal('0'):
                t_type = 'Payment' if amt < Decimal('0') else 'Receipt'
                rows.append(_make_row(date_val, description, ref_fallback,
                                      abs(amt), t_type, balance, i))

    if not rows:
        msg = "No valid transactions found in the file."
        if skipped:
            msg += f" ({skipped} row(s) had unparseable dates and were skipped.)"
        return [], msg

    return rows, None


def _make_row(date: str, description: str, reference: str,
              amount: Decimal, t_type: str,
              balance: Optional[Decimal], row_number: int) -> Dict:
    return {
        'date': date,
        'description': description,
        'reference': reference,
        'amount': amount,
        'transaction_type': t_type,
        'balance': balance,
        'row_number': row_number,
    }


# ---------------------------------------------------------------------------
# Public dispatcher — extend here for OFX / MT940 in future
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {'csv'}


def parse_office_statement(content: bytes, filename: str) -> Tuple[List[Dict], Optional[str]]:
    """
    Parse a bank statement file for Office Account import.
    Returns (rows, error_message).

    Phase 1 supports CSV only.
    To add OFX: add `elif ext == 'ofx': return parse_office_ofx(content)` below.
    """
    ext = (filename.rsplit('.', 1)[-1] if '.' in filename else '').lower().strip()
    if ext == 'csv':
        return parse_office_csv(content)
    return [], (
        f"Unsupported file format '.{ext}'. "
        f"Please upload a CSV file (.csv). "
        "OFX and MT940 support is planned for a future release."
    )
