"""Accumulate Step 5 normalisation soft-skips for C114 digest / failure emails."""

from __future__ import annotations

import logging

_MESSAGES: list[str] = []
logger = logging.getLogger(__name__)


def clear_step5_recovery_messages() -> None:
    _MESSAGES.clear()


def note_step5_recovery(message: str) -> None:
    _MESSAGES.append(message)
    logger.warning("%s", message)


def drain_step5_recovery_messages() -> list[str]:
    out = list(_MESSAGES)
    _MESSAGES.clear()
    return out
