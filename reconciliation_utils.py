"""
Tolerance-based checks for client money reconciliation (SRA-aligned).

Avoids false mismatches from Decimal/float drift; enforces £0.01 tolerance.
"""
from decimal import Decimal, ROUND_HALF_UP

RECONCILIATION_TOLERANCE = Decimal('0.01')


def round_money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def compute_reconciliation_state(ledger_total, cashbook_total, bank_balance):
    """
    Round all amounts to 2dp, then compare with RECONCILIATION_TOLERANCE.

    Returns dict including:
      ledger_cashbook_match, bank_match, variance (zeroed if within tolerance),
      ledger_cashbook_diff_display (absolute diff for mismatch UI, never negative zero),
      can_complete (both matches).
    """
    lt = round_money(ledger_total)
    ct = round_money(cashbook_total)
    bb = round_money(bank_balance)
    tol = RECONCILIATION_TOLERANCE

    variance = round_money(ct - bb)
    if abs(variance) < tol:
        variance = Decimal('0.00')

    ledger_cashbook_match = abs(lt - ct) < tol
    bank_match = abs(ct - bb) < tol

    lcd = round_money(lt - ct)
    if abs(lcd) < tol:
        ledger_cashbook_diff_display = Decimal('0.00')
    else:
        ledger_cashbook_diff_display = abs(lcd)

    return {
        'ledger_total': lt,
        'cashbook_total': ct,
        'bank_balance': bb,
        'variance': variance,
        'ledger_cashbook_match': ledger_cashbook_match,
        'bank_match': bank_match,
        'ledger_cashbook_diff_display': ledger_cashbook_diff_display,
        'can_complete': ledger_cashbook_match and bank_match,
    }


def reconciliation_figures_changed(stored: dict, ledger_total, cashbook_total, bank_balance) -> bool:
    """True if live figures differ from a stored reconciliation snapshot (±£0.01)."""
    tol = RECONCILIATION_TOLERANCE
    stored_lt = round_money(stored.get('ledger_total', 0))
    stored_ct = round_money(stored.get('cashbook_total', 0))
    stored_bb = round_money(stored.get('bank_balance', 0))
    live_lt = round_money(ledger_total)
    live_ct = round_money(cashbook_total)
    live_bb = round_money(bank_balance)
    return (
        abs(stored_lt - live_lt) >= tol
        or abs(stored_ct - live_ct) >= tol
        or abs(stored_bb - live_bb) >= tol
    )
