"""
Strict ISO date handling for solicitor ledger transactions (YYYY-MM-DD only).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger('solicitor.transactions')

ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class ValidationError(ValueError):
    """Invalid date or format (use YYYY-MM-DD only)."""


def parse_transaction_date_strict(raw, field_name: str = 'transaction_date') -> str:
    """
    Accept only YYYY-MM-DD. Raises ValidationError if invalid.
    """
    if raw is None:
        raise ValidationError(f'{field_name}: date is required.')
    s = str(raw).strip()
    if not ISO_DATE_RE.match(s):
        raise ValidationError(f'{field_name}: Invalid date format — use YYYY-MM-DD only.')
    try:
        datetime.strptime(s, '%Y-%m-%d')
    except ValueError:
        raise ValidationError(f'{field_name}: Invalid date format — use YYYY-MM-DD only.') from None
    return s


def log_transaction_date_saved(iso_date: str, context: str = '') -> None:
    """Debug trace for every persisted transaction date."""
    msg = f'Saving transaction with date: {iso_date}'
    if context:
        msg += f' ({context})'
    print(msg)
    logger.info('%s', msg)


def month_outside_current_warning(iso_date: str) -> Optional[str]:
    """
    If the date is not in the current calendar month/year, return a warning for flash.
    """
    dt = datetime.strptime(iso_date, '%Y-%m-%d')
    now = datetime.now()
    if dt.month != now.month or dt.year != now.year:
        return (
            f'⚠ This transaction date is outside the current working month '
            f'({now.strftime("%B %Y")}). Confirm this is intentional.'
        )
    return None
