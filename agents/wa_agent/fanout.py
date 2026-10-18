"""Route structured thread fan-out candidates to the appropriate agent."""
from __future__ import annotations

import logging

from shared.gateway.interface import ChannelInfo
from shared.inbox.interface import Inbox, InboxPost
from shared.wiki.interface import StoredThread, VaultClient

from agents.wa_agent.assign_models import FanoutCandidate
from agents.wa_agent.member_facts import MemberFactService

logger = logging.getLogger(__name__)

PROJECT_CONTEXT = "project_context"
RESEARCH_CONTEXT = "research_context"


class FanoutDispatcher:
    """Deliver candidates idempotently without changing thread ingestion."""

    def __init__(self, vault: VaultClient) -> None:
        self.vault = vault
        self.member_facts = MemberFactService(vault)

    async def dispatch(
        self,
        channel: ChannelInfo,
        thread: StoredThread,
        candidates: list[FanoutCandidate],
    ) -> None:
        project_posts: list[InboxPost] = []
        research_posts: list[InboxPost] = []
        for candidate in candidates:
            if candidate.route == "member_fact":
                try:
                    await self.member_facts.propose(
                        candidate, channel.jid, thread.slug, thread.last_message_at
                    )
                except Exception:  # noqa: BLE001 - one private fact must not stop ingestion
                    logger.exception("member fact fan-out failed for %s", candidate.member_title)
                continue
            post = InboxPost(
                kind=PROJECT_CONTEXT if candidate.route == "project" else RESEARCH_CONTEXT,
                text=candidate.text,
                channel=channel.jid,
                thread_slug=thread.slug,
                thread_link=thread.path.removesuffix(".md"),
                since=(candidate.src_ids[0] if candidate.src_ids else None),
                target=candidate.target or channel.initiative,
                source_ids=candidate.src_ids,
            )
            (project_posts if candidate.route == "project" else research_posts).append(post)
        if project_posts:
            await Inbox(self.vault, "project_agent").post(*project_posts)
        if research_posts:
            # Research Agent is frozen for v1; the inbox is populated so a
            # future consumer can process it without changing ingestion.
            await Inbox(self.vault, "research_agent").post(*research_posts)


__all__ = ["FanoutDispatcher", "PROJECT_CONTEXT", "RESEARCH_CONTEXT"]
