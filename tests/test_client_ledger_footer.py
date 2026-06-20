"""Client ledger footer totals must match running-balance eligibility rules."""
from decimal import Decimal

from database import _ledger_row_counts_toward_running_balance


def _footer_totals(transactions, fee_transfer_ledger_ids=None):
    """Mirror client_ledger.html footer credit/debit accumulation."""
    fee_transfer_ledger_ids = fee_transfer_ledger_ids or set()
    tot_cli_dr = Decimal('0.00')
    tot_cli_cr = Decimal('0.00')
    tot_off_cr = Decimal('0.00')
    for trans in transactions:
        if not _ledger_row_counts_toward_running_balance(trans):
            continue
        amt = Decimal(str(trans['amount']))
        is_receipt = trans.get('transaction_type') == 'Receipt'
        is_fee_xfr = trans.get('id') in fee_transfer_ledger_ids
        if is_receipt:
            tot_cli_cr += amt
        else:
            tot_cli_dr += amt
            if is_fee_xfr:
                tot_off_cr += amt
    return tot_cli_dr, tot_cli_cr, tot_off_cr


def _running_balance(transactions):
    balance = Decimal('0.00')
    for trans in transactions:
        if not _ledger_row_counts_toward_running_balance(trans):
            continue
        amt = Decimal(str(trans['amount']))
        if trans.get('transaction_type') == 'Receipt':
            balance += amt
        elif trans.get('transaction_type') in ('Payment', 'Transfer'):
            balance -= amt
    return balance


def test_footer_credits_exclude_declined_receipt():
    """Declined receipts must not inflate Client Account CR footer total."""
    transactions = [
        {
            'id': 1,
            'transaction_type': 'Receipt',
            'amount': 2000,
            'cashbook_status': 'Cleared',
            'reversal_status': 'ACTIVE',
            'reversal_depth': 0,
            'linked_cashbook_id': 1,
        },
        {
            'id': 2,
            'transaction_type': 'Receipt',
            'amount': 3000,
            'cashbook_status': 'Declined',
            'reversal_status': 'ACTIVE',
            'reversal_depth': 0,
            'linked_cashbook_id': 2,
        },
        {
            'id': 3,
            'transaction_type': 'Receipt',
            'amount': 5000,
            'cashbook_status': 'Cleared',
            'reversal_status': 'ACTIVE',
            'reversal_depth': 0,
            'linked_cashbook_id': 3,
        },
        {
            'id': 4,
            'transaction_type': 'Receipt',
            'amount': 100,
            'cashbook_status': 'Cleared',
            'reversal_status': 'ACTIVE',
            'reversal_depth': 0,
            'linked_cashbook_id': 4,
        },
    ]

    tot_dr, tot_cr, _ = _footer_totals(transactions)
    balance = _running_balance(transactions)

    assert tot_cr == Decimal('7100.00')
    assert tot_dr == Decimal('0.00')
    assert balance == Decimal('7100.00')
    assert tot_cr - tot_dr == balance


def test_footer_excludes_reversed_and_pending_receipts():
    transactions = [
        {
            'id': 1,
            'transaction_type': 'Receipt',
            'amount': 1000,
            'cashbook_status': 'Pending',
            'reversal_status': 'ACTIVE',
            'reversal_depth': 0,
            'linked_cashbook_id': 1,
        },
        {
            'id': 2,
            'transaction_type': 'Receipt',
            'amount': 500,
            'cashbook_status': 'Cleared',
            'reversal_status': 'REVERSED',
            'reversal_depth': 0,
            'linked_cashbook_id': 2,
        },
        {
            'id': 3,
            'transaction_type': 'Receipt',
            'amount': 250,
            'cashbook_status': 'Cleared',
            'reversal_status': 'ACTIVE',
            'reversal_depth': 0,
            'linked_cashbook_id': 3,
        },
    ]

    tot_dr, tot_cr, _ = _footer_totals(transactions)
    assert tot_cr == Decimal('250.00')
    assert tot_dr == Decimal('0.00')
