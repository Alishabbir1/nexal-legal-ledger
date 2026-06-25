"""Structured SSO stage tracing for production debugging."""
from __future__ import annotations

import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

TRACE_EMAILS = frozenset({"sunthessmunir@gmail.com"})


def should_trace(email: str | None) -> bool:
    return bool(email and email.strip().lower() in TRACE_EMAILS)


def sso_stage(email: str | None, stage: str, fn: Callable[[], T]) -> T:
    """Run an SSO step; log stage boundaries for traced accounts."""
    if should_trace(email):
        logger.warning("SSO_TRACE email=%s stage=%s status=start", email, stage)
    try:
        result = fn()
    except Exception as exc:
        if should_trace(email):
            logger.exception(
                "SSO_TRACE email=%s stage=%s status=failed error=%s",
                email,
                stage,
                exc,
            )
        raise
    if should_trace(email):
        detail = result if isinstance(result, (str, int, bool)) else None
        logger.warning(
            "SSO_TRACE email=%s stage=%s status=ok%s",
            email,
            stage,
            f" detail={detail!r}" if detail is not None else "",
        )
    return result


def log_sso_detail(email: str | None, stage: str, **fields: Any) -> None:
    if should_trace(email):
        logger.warning(
            "SSO_TRACE email=%s stage=%s %s",
            email,
            stage,
            " ".join(f"{key}={value!r}" for key, value in fields.items()),
        )
