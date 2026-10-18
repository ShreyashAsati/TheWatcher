from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from agents.wa_agent.assign_models import FanoutCandidate
from agents.wa_agent.fanout import FanoutDispatcher
from agents.wa_agent.member_facts import MemberFactService
from shared.confirmations.interface import get_pending_confirmation
from shared.gateway.interface import ChannelInfo, InboundMessage
from shared.wiki.interface import LocalDirClient

MEMBER_PAGE = (
    "---\n"
    "type: member\nslug: devansh\ntitle: Devansh\nrole: Executive\n"
    "status: active\nwhatsapp: linked\n---\n# Devansh\n"
    "## About\nBuilds things.\n"
    "## Projects\n<!-- watcher:derived -->\n<!-- /watcher -->\n"
    "## Past projects\n<!-- watcher:derived -->\n<!-- /watcher -->\n"
    "## Open tasks\n<!-- watcher:derived -->\n<!-- /watcher -->\n"
    "## Notes\n\n"
)


THREAD_PAGE = type(
    "Thread",
    (),
    {
        "slug": "t1",
        "path": "channels/chat/t1.md",
        "last_message_at": datetime(2026, 10, 18, tzinfo=UTC),
    },
)()


@pytest.fixture
def vault(tmp_path: Path) -> LocalDirClient:
    return LocalDirClient(tmp_path)


async def test_member_fact_is_held_until_confirmation_and_appended(vault, monkeypatch):
    await vault.create("people/devansh.md", MEMBER_PAGE)
    sent: list[tuple[str, str]] = []

    async def fake_send(channel: str, text: str, reply_to: str | None = None):
        from shared.gateway.types import SendResult

        sent.append((channel, text))
        return SendResult(message_id="confirm-msg-1")

    monkeypatch.setattr("agents.wa_agent.member_facts.gateway_send", fake_send)
    service = MemberFactService(vault)
    candidate = FanoutCandidate(
        route="member_fact",
        text="Devansh is attending a conference",
        src_ids=["msg-1"],
        member_title="Devansh",
        member_identity="devansh@s.whatsapp.net",
        temporal_scope="temporary",
        valid_until="2026-10-25",
    )
    assert await service.propose(candidate, "chat@g.us", "t1") == "pending"
    assert sent and sent[0][0] == "devansh@s.whatsapp.net"
    before = (await vault.read("people/devansh.md")).content
    assert "conference" not in before

    await service.handle_message(
        InboundMessage(
            row_id=1,
            message_id="reply-1",
            channel="devansh@s.whatsapp.net",
            received_at=datetime.now(UTC),
            sent_at=datetime.now(UTC),
            text="OK",
            sender="devansh@s.whatsapp.net",
        )
    )
    after = (await vault.read("people/devansh.md")).content
    assert "conference" in after
    assert "valid until 2026-10-25" in after
    assert await get_pending_confirmation("devansh@s.whatsapp.net", "devansh@s.whatsapp.net") is None


async def test_member_fact_reaction_confirms(vault, monkeypatch):
    await vault.create("people/devansh.md", MEMBER_PAGE)

    async def fake_send(channel: str, text: str, reply_to: str | None = None):
        from shared.gateway.types import SendResult

        return SendResult(message_id="confirm-msg-2")

    monkeypatch.setattr("agents.wa_agent.member_facts.gateway_send", fake_send)
    service = MemberFactService(vault)
    candidate = FanoutCandidate(
        route="member_fact",
        text="Devansh went to a conference",
        src_ids=["msg-2"],
        member_title="Devansh",
        member_identity="devansh2@s.whatsapp.net",
        temporal_scope="historical",
    )
    await service.propose(candidate, "chat@g.us", "t1")
    await service.handle_reaction("devansh2@s.whatsapp.net", "confirm-msg-2", "⭐")
    assert "conference" in (await vault.read("people/devansh.md")).content


async def test_project_and_research_candidates_are_inboxed(vault):
    dispatcher = FanoutDispatcher(vault)
    channel = ChannelInfo("chat@g.us", "Chat", "project", "Watcher")
    await dispatcher.dispatch(
        channel,
        THREAD_PAGE,
        [
            FanoutCandidate(route="project", text="The launch is on Friday", src_ids=["m1"]),
            FanoutCandidate(route="research", text="A useful paper was shared", src_ids=["m2"]),
        ],
    )
    from shared.inbox.interface import Inbox

    project = await Inbox(vault, "project_agent").pending()
    research = await Inbox(vault, "research_agent").pending()
    assert project[0].text == "The launch is on Friday"
    assert project[0].target == "Watcher"
    assert research[0].text == "A useful paper was shared"
