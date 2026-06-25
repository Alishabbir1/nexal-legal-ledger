"""Ensure ledger CLI scripts can import packages from the repository root."""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bootstrap_repo_root() -> str:
    """Insert repo root on sys.path for CLI imports without changing cwd."""
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    return _REPO_ROOT
