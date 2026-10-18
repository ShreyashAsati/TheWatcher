from __future__ import annotations

import hashlib
import hmac
import json

from shared.config import settings
from shared.confirmations.interface import bind_confirmation_message, create_pending_fact
from shared.gateway.runtime import GatewayRuntime
from shared.gateway.webhook import receive_webhook
from shared.wiki.interface import member_page_path

from tests.gowa_payloads import message_event, reaction_event


def _sign(body: bytes) -> str:
    digest = hmac.new(settings.gowa_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _pending() -> None:
    confirmation = await create_pending_fact(
        dedupe_key="dm-test-key",
        member_identity="member-dm@s.whatsapp.net",
        member_title="Member DM",
        fact="attending a conference",
        source_ids=["source-1"],
        source_channel="chat@g.us",
        source_thread="thread-1",
        temporal_scope="temporary",
        valid_until=None,
        sensitive=False,
        wiki_path=member_page_path("Member DM"),
        rendered_line="- attending a conference [src:: source-1]",
        dm_channel="member-dm@s.whatsapp.net",
    )
    assert confirmation is not None
    await bind_confirmation_message(confirmation.id, "confirm-dm-msg")


async def test_normal_dm_is_rejected_and_scoped_confirmation_is_dispatched():
    runtime = GatewayRuntime()
    seen: list[str] = []

    async def hook(message):
        seen.append(message.text)

    runtime.hooks.register_direct_message_hook(hook)
    ordinary = message_event(
        "dm-ordinary",
        "ordinary@s.whatsapp.net",
        "hello",
        sender="ordinary@s.whatsapp.net",
    )
    ordinary_body = json.dumps(ordinary).encode()
    assert await receive_webhook(ordinary_body, _sign(ordinary_body), runtime) is False

    await _pending()
    scoped = message_event(
        "dm-confirm", "member-dm@s.whatsapp.net", "OK", sender="member-dm@s.whatsapp.net"
    )
    body = json.dumps(scoped).encode()
    assert await receive_webhook(body, _sign(body), runtime) is True
    assert seen == ["OK"]


async def test_scoped_confirmation_reaction_is_dispatched():
    runtime = GatewayRuntime()
    seen: list[tuple[str, str | None, str | None]] = []

    async def hook(sender, message_id, emoji):
        seen.append((sender, message_id, emoji))

    runtime.hooks.register_direct_reaction_hook(hook)
    await _pending()
    payload = reaction_event(
        "dm-reaction", "member-dm@s.whatsapp.net", "member-dm@s.whatsapp.net", "⭐", "confirm-dm-msg"
    )
    body = json.dumps(payload).encode()
    assert await receive_webhook(body, _sign(body), runtime) is True
    assert seen == [("member-dm@s.whatsapp.net", "confirm-dm-msg", "⭐")]
