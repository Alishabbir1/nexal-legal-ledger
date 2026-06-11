"""
Reconciliation matching engine.
Compares bank entries against Cashbook transactions.
Classifies: MATCHED, NEEDS_REVIEW, MISSING_IN_LEDGER, MISSING_IN_BANK, AMOUNT_MISMATCH, DUPLICATE_DETECTED.
Never modifies records automatically.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Optional
import difflib

STATUS_MATCHED = 'MATCHED'
STATUS_NEEDS_REVIEW = 'NEEDS_REVIEW'
STATUS_MISSING_IN_LEDGER = 'MISSING_IN_LEDGER'
STATUS_MISSING_IN_BANK = 'MISSING_IN_BANK'
STATUS_AMOUNT_MISMATCH = 'AMOUNT_MISMATCH'
STATUS_DUPLICATE_DETECTED = 'DUPLICATE_DETECTED'

DATE_TOLERANCE_DAYS = 1


def _to_decimal(val) -> Decimal:
    if val is None:
        return Decimal('0')
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal('0')


def _norm_ref(s: str) -> str:
    return (s or '').strip().upper().replace(' ', '')


def _ref_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    na, nb = _norm_ref(a), _norm_ref(b)
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(str(s)[:10], fmt)
        except ValueError:
            continue
    return None


def _dates_within_tolerance(d1: str, d2: str) -> bool:
    a = _parse_date(d1)
    b = _parse_date(d2)
    if not a or not b:
        return False
    delta = abs((a - b).days)
    return delta <= DATE_TOLERANCE_DAYS


def _cashbook_signed_amount(cb: Dict) -> Decimal:
    amt = _to_decimal(cb.get('amount'))
    if (cb.get('transaction_type') or '').lower() == 'payment':
        return -amt
    return amt


def _bank_amount_direction(amount: float) -> int:
    """+1 receipt, -1 payment."""
    return 1 if float(amount) >= 0 else -1


def _cashbook_direction(cb: Dict) -> int:
    return 1 if (cb.get('transaction_type') or '').lower() == 'receipt' else -1


def run_matching(
    bank_entries: List[Dict],
    cashbook_transactions: List[Dict],
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
) -> List[Dict]:
    """
    Match bank entries to cashbook. Returns list of match results.
    Each result: bank_date, bank_amount, bank_desc, bank_ref, ledger_entry, status, suggested_action
    """
    # Filter cashbook by date range
    if date_from or date_to:
        filtered_cb = []
        for cb in cashbook_transactions:
            if cb.get('status') == 'Declined':
                continue
            dt = cb.get('transaction_date')
            if date_from and dt and dt < date_from:
                continue
            if date_to and dt and dt > date_to:
                continue
            filtered_cb.append(cb)
    else:
        filtered_cb = [c for c in cashbook_transactions if c.get('status') != 'Declined']

    results = []
    used_cashbook_ids = set()

    for be in bank_entries:
        bd = be.get('date', '')
        bamt = _to_decimal(be.get('amount', 0))
        bdesc = be.get('description', '')
        bref = be.get('reference', '') or bdesc[:50]
        bdir = _bank_amount_direction(float(bamt))

        best_match = None
        best_score = -1
        candidates = []

        for cb in filtered_cb:
            if cb['id'] in used_cashbook_ids:
                continue
            cb_amt = _to_decimal(cb.get('amount'))
            cb_dir = _cashbook_direction(cb)
            if bdir != cb_dir:
                continue
            amt_match = abs(cb_amt - abs(bamt)) < Decimal('0.01')
            date_ok = _dates_within_tolerance(bd, cb.get('transaction_date', ''))
            ref_sim = _ref_similarity(bref, cb.get('reference', '') or cb.get('description', ''))

            score = 0
            if amt_match:
                score += 40
            if date_ok:
                score += 30
            if ref_sim > 0.7:
                score += 20
            elif ref_sim > 0.3:
                score += 10
            if amt_match and date_ok and ref_sim > 0.5:
                score += 20

            if score > best_score:
                best_score = score
                best_match = cb
                candidates.append((cb, score, amt_match, date_ok, ref_sim))

        ledger_display = None
        status = STATUS_MISSING_IN_LEDGER
        suggested_action = "Add missing cashbook/ledger entry"

        if best_match and best_score >= 50:
            # Get metrics for best match
            amt_ok = abs(_to_decimal(best_match.get('amount')) - abs(bamt)) < Decimal('0.01')
            date_ok = _dates_within_tolerance(bd, best_match.get('transaction_date', ''))
            ref_ok = _ref_similarity(bref, best_match.get('reference', '') or best_match.get('description', '')) > 0.5
            dup_count = sum(1 for c, _, _, _, _ in candidates if c['id'] == best_match['id'])
            if dup_count > 1:
                status = STATUS_DUPLICATE_DETECTED
                suggested_action = "Review for duplicate bank entry"
            elif not amt_ok:
                status = STATUS_AMOUNT_MISMATCH
                suggested_action = "Verify amounts; create correction if needed"
            elif not date_ok or not ref_ok:
                status = STATUS_NEEDS_REVIEW
                suggested_action = "Confirm match (date/reference variance)"
            else:
                status = STATUS_MATCHED
                suggested_action = "No action"
                used_cashbook_ids.add(best_match['id'])

            ref = best_match.get('reference', '')
            client = best_match.get('client_code', '') or ''
            ledger_display = f"{ref} | {client} | £{best_match.get('amount')}"

        results.append({
            'bank_date': bd,
            'bank_amount': str(bamt),
            'bank_description': bdesc,
            'bank_reference': bref,
            'ledger_entry': ledger_display,
            'cashbook_id': best_match['id'] if best_match else None,
            'ledger_id': best_match.get('linked_ledger_id') if best_match else None,
            'status': status,
            'suggested_action': suggested_action,
            'bank_row': be.get('row'),
        })

    # MISSING_IN_BANK: cashbook entries not matched to any bank entry
    for cb in filtered_cb:
        if cb['id'] in used_cashbook_ids:
            continue
        results.append({
            'bank_date': '-',
            'bank_amount': '-',
            'bank_description': '-',
            'bank_reference': '-',
            'ledger_entry': f"{cb.get('reference', '')} | {cb.get('client_code', '') or 'Office'} | £{cb.get('amount')}",
            'cashbook_id': cb['id'],
            'ledger_id': cb.get('linked_ledger_id'),
            'status': STATUS_MISSING_IN_BANK,
            'suggested_action': "Verify bank statement; add if missing from upload",
            'bank_row': None,
        })

    return results


def get_summary(results: List[Dict]) -> Dict:
    """Count by status."""
    counts = {
        'matched': 0,
        'review': 0,
        'problems': 0,
        'total': len(results),
    }
    for r in results:
        s = r.get('status', '')
        if s == STATUS_MATCHED:
            counts['matched'] += 1
        elif s == STATUS_NEEDS_REVIEW:
            counts['review'] += 1
        else:
            counts['problems'] += 1
    return counts
