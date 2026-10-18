"""Transport types for the confirmation store."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingFact:
    update_id: int
    member_identity: str
    member_title: str
    fact: str
    source_ids: tuple[str, ...]
    source_channel: str
    source_thread: str | None
    temporal_scope: str
    valid_until: str | None
    sensitive: bool
    wiki_path: str
    rendered_line: str
    status: str


@dataclass(frozen=True)
class Confirmation:
    id: int
    update_id: int
    member_identity: str
    dm_channel: str
    confirmation_message_id: str | None
    status: str
    fact: PendingFact
