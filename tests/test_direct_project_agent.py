from __future__ import annotations

from pathlib import Path

import pytest
from agents.project_agent import interface as project_agent
from shared.db import BotAdmin, Channel, get_session
from shared.inbox.interface import Inbox, InboxPost
from shared.models.schemas import TextResult
from shared.models.text import TextModelClient
from shared.wiki.interface import LocalDirClient
from sqlalchemy import select

PROJECT_PAGE = (
    "---\n"
    "type: project\nslug: watcher\ntitle: Watcher\nstatus: active\n"
    'lead: "[[Devansh]]"\n---\n# Watcher\n'
    "## Brief\nBot project.\n"
    "## Status\n<!-- watcher:managed -->\nold\n<!-- /watcher -->\n"
    "## Open tasks\n<!-- watcher:managed -->\n<!-- /watcher -->\n"
    "## Decisions\n<!-- watcher:append -->\n<!-- /watcher -->\n"
    "## Log\n<!-- watcher:append -->\n<!-- /watcher -->\n"
    "## Resources\n<!-- watcher:append -->\n<!-- /watcher -->\n"
    "## Threads\n<!-- watcher:derived -->\n<!-- /watcher -->\n"
    "## Notes\n"
)


class Worker(TextModelClient):
    def __init__(self, text: str = "Updated status."):
        self.model_name = "direct-test"
        self.text = text
        self.prompts: list[str] = []

    async def generate(self, prompt: str, system: str | None = None):  # type: ignore[override]
        self.prompts.append(prompt)
        return TextResult(text=self.text, input_tokens=1, output_tokens=1)


@pytest.fixture
def vault(tmp_path: Path) -> LocalDirClient:
    return LocalDirClient(tmp_path)


async def _seed(vault: LocalDirClient) -> None:
    async with get_session() as session:
        existing = await session.scalar(select(Channel).where(Channel.jid == "direct-project@g.us"))
        if existing is None:
            session.add(
                Channel(jid="direct-project@g.us", kind="project", title="Watcher", initiative="Watcher")
            )
        await session.commit()
    await vault.create("projects/watcher.md", PROJECT_PAGE)


async def test_direct_read_does_not_consume_project_inbox(vault):
    await _seed(vault)
    await Inbox(vault, "project_agent").post(
        InboxPost(kind="request", text="leave me pending", channel="direct-project@g.us")
    )
    answer = await project_agent.run_project_agent_direct(
        vault, Worker("The current status is old."), "direct-project@g.us", "What is the status?", "member@x"
    )
    assert answer == "The current status is old."
    assert len(await Inbox(vault, "project_agent").pending()) == 1


async def test_direct_read_uses_wiki_context_from_an_unlinked_chat(vault):
    await vault.create("projects/previous.md", PROJECT_PAGE.replace("Watcher", "Previous"))
    worker = Worker("A previous project exists.")
    answer = await project_agent.run_project_agent_direct(
        vault, worker, "general-chat@g.us", "Was a previous project recorded?", "member@x"
    )
    assert answer == "A previous project exists."
    assert "projects/previous.md" in worker.prompts[0]


async def test_direct_write_requires_admin_or_lead(vault):
    await _seed(vault)
    denied = await project_agent.run_project_agent_direct(
        vault, Worker(), "direct-project@g.us", "mark the project active", "member@x", write=True
    )
    assert "require" in denied
    assert "old" in (await vault.read("projects/watcher.md")).content


async def test_direct_write_for_bot_admin_bypasses_inbox(vault):
    await _seed(vault)
    async with get_session() as session:
        session.add(BotAdmin(wa_identity="admin@x"))
        await session.commit()
    result = await project_agent.run_project_agent_direct(
        vault,
        Worker("Admin-approved status."),
        "direct-project@g.us",
        "update the status",
        "admin@x",
        write=True,
    )
    assert "Updated Watcher" in result
    assert "Admin-approved status." in (await vault.read("projects/watcher.md")).content
    assert await Inbox(vault, "project_agent").pending() == []
