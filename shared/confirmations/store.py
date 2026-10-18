"""SQL-backed implementation of the confirmation interface."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from shared.confirmations.models import Confirmation, PendingFact
from shared.db import MemberFactConfirmation, PendingWikiUpdate, get_session


def _fact(row: PendingWikiUpdate) -> PendingFact:
    return PendingFact(
        update_id=row.id,
        member_identity=row.member_identity,
        member_title=row.member_title,
        fact=row.fact,
        source_ids=tuple(json.loads(row.source_ids)),
        source_channel=row.source_channel,
        source_thread=row.source_thread,
        temporal_scope=row.temporal_scope,
        valid_until=row.valid_until,
        sensitive=row.sensitive,
        wiki_path=row.wiki_path,
        rendered_line=row.rendered_line,
        status=row.status,
    )


def _confirmation(row: MemberFactConfirmation, update: PendingWikiUpdate) -> Confirmation:
    return Confirmation(
        id=row.id,
        update_id=row.update_id,
        member_identity=row.member_identity,
        dm_channel=row.dm_channel,
        confirmation_message_id=row.confirmation_message_id,
        status=row.status,
        fact=_fact(update),
    )


async def create_pending_fact(
    *,
    dedupe_key: str,
    member_identity: str,
    member_title: str,
    fact: str,
    source_ids: list[str],
    source_channel: str,
    source_thread: str | None,
    temporal_scope: str,
    valid_until: str | None,
    sensitive: bool,
    wiki_path: str,
    rendered_line: str,
    dm_channel: str,
    expires_in_days: int = 14,
) -> Confirmation | None:
    """Create one pending fact and its private confirmation atomically.

    ``None`` means an identical pending or already resolved fact exists.
    """
    now = datetime.now(UTC)
    async with get_session() as session:
        existing = await session.scalar(
            select(PendingWikiUpdate).where(PendingWikiUpdate.dedupe_key == dedupe_key)
        )
        if existing is not None:
            confirmation = await session.scalar(
                select(MemberFactConfirmation).where(
                    MemberFactConfirmation.update_id == existing.id,
                    MemberFactConfirmation.status == "pending",
                )
            )
            return _confirmation(confirmation, existing) if confirmation else None
        update = PendingWikiUpdate(
            dedupe_key=dedupe_key,
            member_identity=member_identity,
            member_title=member_title,
            fact=fact,
            source_ids=json.dumps(source_ids),
            source_channel=source_channel,
            source_thread=source_thread,
            temporal_scope=temporal_scope,
            valid_until=valid_until,
            sensitive=sensitive,
            wiki_path=wiki_path,
            rendered_line=rendered_line,
            expires_at=now + timedelta(days=expires_in_days),
        )
        session.add(update)
        await session.flush()
        confirmation = MemberFactConfirmation(
            update_id=update.id,
            member_identity=member_identity,
            dm_channel=dm_channel,
        )
        session.add(confirmation)
        await session.commit()
        await session.refresh(confirmation)
        return _confirmation(confirmation, update)


async def bind_confirmation_message(confirmation_id: int, message_id: str | None) -> None:
    async with get_session() as session:
        row = await session.get(MemberFactConfirmation, confirmation_id)
        if row is not None:
            row.confirmation_message_id = message_id
            await session.commit()


async def get_pending_confirmation(
    member_identity: str, dm_channel: str | None = None
) -> Confirmation | None:
    async with get_session() as session:
        stmt = (
            select(MemberFactConfirmation, PendingWikiUpdate)
            .join(PendingWikiUpdate, PendingWikiUpdate.id == MemberFactConfirmation.update_id)
            .where(
                MemberFactConfirmation.member_identity == member_identity,
                MemberFactConfirmation.status == "pending",
                PendingWikiUpdate.status == "pending",
            )
            .order_by(MemberFactConfirmation.created_at)
        )
        if dm_channel is not None:
            stmt = stmt.where(MemberFactConfirmation.dm_channel == dm_channel)
        row = (await session.execute(stmt)).first()
    return _confirmation(row[0], row[1]) if row else None


async def get_confirmation_for_message(message_id: str) -> Confirmation | None:
    async with get_session() as session:
        stmt = (
            select(MemberFactConfirmation, PendingWikiUpdate)
            .join(PendingWikiUpdate, PendingWikiUpdate.id == MemberFactConfirmation.update_id)
            .where(
                MemberFactConfirmation.confirmation_message_id == message_id,
                MemberFactConfirmation.status == "pending",
                PendingWikiUpdate.status == "pending",
            )
        )
        row = (await session.execute(stmt)).first()
    return _confirmation(row[0], row[1]) if row else None


async def resolve_confirmation(
    confirmation_id: int, status: str, resolved_by: str
) -> Confirmation | None:
    if status not in {"confirmed", "rejected", "expired"}:
        raise ValueError(f"unknown confirmation status: {status}")
    now = datetime.now(UTC)
    async with get_session() as session:
        confirmation = await session.get(MemberFactConfirmation, confirmation_id)
        if confirmation is None:
            return None
        update = await session.get(PendingWikiUpdate, confirmation.update_id)
        if update is None or confirmation.status != "pending" or update.status != "pending":
            return None
        confirmation.status = status
        confirmation.resolved_at = now
        confirmation.resolved_by = resolved_by
        update.status = status
        update.resolved_at = now
        update.resolved_by = resolved_by
        await session.commit()
        await session.refresh(confirmation)
        await session.refresh(update)
        return _confirmation(confirmation, update)
