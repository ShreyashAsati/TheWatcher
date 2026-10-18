"""Public confirmation store used by the WhatsApp Agent and Gateway.

The rows are durable waits (ADR-0007), while the wiki remains the source of
truth for the eventual member page. No caller imports the SQLAlchemy models
directly.
"""
from __future__ import annotations

from shared.confirmations.models import Confirmation, PendingFact
from shared.confirmations.store import (
    bind_confirmation_message,
    create_pending_fact,
    get_confirmation_for_message,
    get_pending_confirmation,
    resolve_confirmation,
)

__all__ = [
    "Confirmation",
    "PendingFact",
    "bind_confirmation_message",
    "create_pending_fact",
    "get_confirmation_for_message",
    "get_pending_confirmation",
    "resolve_confirmation",
]
