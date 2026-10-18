"""Member-fact fan-out and the narrowly scoped DM confirmation flow."""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from shared.confirmations.interface import (
    Confirmation,
    bind_confirmation_message,
    create_pending_fact,
    get_confirmation_for_message,
    get_pending_confirmation,
    resolve_confirmation,
)
from shared.gateway.interface import InboundMessage
from shared.gateway.interface import send as gateway_send
from shared.wiki.interface import (
    ConflictError,
    VaultClient,
    append_lines,
    dump_page,
    member_page_path,
    parse_page,
    read_if_exists,
    repair_missing_sections,
)

from agents.wa_agent.assign_models import FanoutCandidate

_APPROVE = {"ok", "okay", "yes", "y", "confirm", "confirmed", "👍", "✅"}
_REJECT = {"no", "n", "reject", "rejected", "cancel", "❌"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).strip("-• ")


def render_fact_line(candidate: FanoutCandidate, observed_on: datetime | None = None) -> str:
    observed = (observed_on or datetime.now(UTC)).date().isoformat()
    scope = candidate.temporal_scope if candidate.temporal_scope != "unknown" else "ongoing"
    suffix = f"; valid until {candidate.valid_until}" if candidate.valid_until else ""
    source = ", ".join(dict.fromkeys(candidate.src_ids))
    return f"- {observed} ({scope}{suffix}): {_clean(candidate.text)} [src:: {source}]"


class MemberFactService:
    def __init__(self, vault: VaultClient) -> None:
        self.vault = vault

    async def propose(
        self,
        candidate: FanoutCandidate,
        channel: str,
        thread_slug: str | None,
        observed_on: datetime | None = None,
    ) -> str:
        if candidate.route != "member_fact" or not candidate.member_identity or not candidate.member_title:
            return "ignored"
        text = _clean(candidate.text)
        if not text or not candidate.src_ids:
            return "ignored"
        path = member_page_path(candidate.member_title)
        if await read_if_exists(self.vault, path) is None:
            return "ignored"
        line = render_fact_line(candidate, observed_on)
        digest = hashlib.sha256(
            f"{candidate.member_identity}|{path}|{text.lower()}|{','.join(sorted(candidate.src_ids))}".encode()
        ).hexdigest()
        confirmation = await create_pending_fact(
            dedupe_key=digest,
            member_identity=candidate.member_identity,
            member_title=candidate.member_title,
            fact=text,
            source_ids=candidate.src_ids,
            source_channel=channel,
            source_thread=thread_slug,
            temporal_scope=(candidate.temporal_scope if candidate.temporal_scope != "unknown" else "ongoing"),
            valid_until=candidate.valid_until,
            sensitive=candidate.sensitive,
            wiki_path=path,
            rendered_line=line,
            dm_channel=candidate.member_identity,
        )
        if confirmation is None:
            return "duplicate"
        if confirmation.confirmation_message_id is None:
            sensitivity = " (sensitive personal information)" if candidate.sensitive else ""
            validity = f" Valid until {candidate.valid_until}." if candidate.valid_until else ""
            prompt = (
                f"I found this fact for your wiki page{sensitivity}:\n"
                f"{text}\n\nReply OK or react to confirm, or reply NO to discard.{validity}"
            )
            result = await gateway_send(candidate.member_identity, prompt)
            await bind_confirmation_message(confirmation.id, result.message_id)
        return "pending"

    async def handle_message(self, message: InboundMessage) -> None:
        confirmation = await get_pending_confirmation(message.sender, message.channel)
        if confirmation is None or not message.text.strip():
            return
        decision = message.text.strip().casefold()
        if decision in _APPROVE:
            await self._resolve(confirmation, "confirmed", message.sender)
        elif decision in _REJECT:
            await self._resolve(confirmation, "rejected", message.sender)

    async def handle_reaction(
        self, sender: str, reacted_message_id: str | None, emoji: str | None
    ) -> None:
        if not reacted_message_id or not emoji:
            return
        confirmation = await get_confirmation_for_message(reacted_message_id)
        if confirmation is None or confirmation.member_identity != sender:
            return
        await self._resolve(confirmation, "confirmed", sender)

    async def _resolve(self, confirmation: Confirmation, status: str, resolved_by: str) -> None:
        if status == "rejected":
            await resolve_confirmation(confirmation.id, status, resolved_by)
            await gateway_send(confirmation.dm_channel, "Discarded; I will not update your member page.")
            return
        page_result = await read_if_exists(self.vault, confirmation.fact.wiki_path)
        if page_result is None:
            return
        page = repair_missing_sections(parse_page(page_result.content))
        section = page.section("Facts")
        if section is not None and confirmation.fact.rendered_line in section.body:
            await resolve_confirmation(confirmation.id, status, resolved_by)
            return
        updated = append_lines(page, "Facts", [confirmation.fact.rendered_line])
        try:
            await self.vault.write(
                confirmation.fact.wiki_path,
                dump_page(updated),
                base_revision=page_result.revision,
            )
        except ConflictError:
            return
        await resolve_confirmation(confirmation.id, status, resolved_by)
        await gateway_send(confirmation.dm_channel, "Confirmed. I added that fact to your member page.")


__all__ = ["MemberFactService", "render_fact_line"]
