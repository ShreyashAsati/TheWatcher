"""JSON contracts and small result types for structured assignment."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from agents.wa_agent.messages import BufferedMessage

CHATTER = "chatter"
NEW_THREAD = "new-thread"


class NewThread(BaseModel):
    id: str = Field(description='"new-1", "new-2", ... numbered from the index given in the prompt')
    title: str = Field(description="At most 5 plain words naming the concrete topic")
    description: str = Field(description="1-2 sentences: what this thread is about")


class Assignment(BaseModel):
    message_id: str = Field(description="The id= value of one message from the prompt")
    thread: str = Field(description='An existing thread slug, a "new-N" id you declared, or "chatter"')


class AssignmentResponse(BaseModel):
    new_threads: list[NewThread] = []
    assignments: list[Assignment]


class ItemOut(BaseModel):
    kind: Literal["task", "decision", "resource", "question"]
    text: str
    src_ids: list[str] = Field(description="Message ids (from [src:: ...]) this item comes from")
    owner: str | None = Field(default=None, description="A member name exactly as listed in the prompt")
    due: str | None = Field(default=None, description="YYYY-MM-DD, only if stated")
    done: bool = False
    block_id: str | None = Field(default=None, description="Reuse an existing item's ^id when updating it")


class FanoutCandidate(BaseModel):
    """A source-grounded fact/context item for another agent.

    The model may identify a destination, but the application validates the
    source ids and member target before anything is delivered or written.
    """

    route: Literal["project", "research", "member_fact"]
    text: str = Field(description="A concise fact or context statement grounded in source messages")
    src_ids: list[str] = Field(default_factory=list, description="Message ids supporting this statement")
    target: str | None = Field(default=None, description="Project or event title when explicitly named")
    member_title: str | None = Field(default=None, description="Linked member title for member_fact")
    member_identity: str | None = Field(
        default=None, description="Filled by the application, never guessed by the model"
    )
    temporal_scope: Literal["ongoing", "temporary", "historical", "unknown"] = "unknown"
    valid_until: str | None = Field(
        default=None, description="YYYY-MM-DD only when the message gives an end date"
    )
    sensitive: bool = Field(
        default=False, description="Sensitive personal fact; always requires confirmation"
    )


class ThreadUpdate(BaseModel):
    title: str
    summary: str
    items: list[ItemOut] = []
    fanout: list[FanoutCandidate] = Field(
        default_factory=list,
        description=(
            "Optional source-grounded context for project, research, or member confirmation workflows"
        ),
    )


@dataclass
class SenderNames:
    """Per-batch resolution of sender jid -> wiki member title."""

    members: dict[str, str | None] = field(default_factory=dict)

    def wiki_name(self, m: BufferedMessage) -> str:
        title = self.members.get(m.sender)
        if title:
            return f"[[{title}]]"
        return m.sender_name or m.sender

    def prompt_name(self, m: BufferedMessage) -> str:
        title = self.members.get(m.sender)
        name = title or m.sender_name
        return f"{name} ({m.sender})" if name else m.sender

    def prompt_label(self, m: BufferedMessage) -> str:
        """Speaker token a model sees. A linked member is already their
        [[wikilink]]; an unlinked sender keeps the display name and jid."""
        title = self.members.get(m.sender)
        if title:
            return f"[[{title}]]"
        return self.prompt_name(m)

    def member_titles(self, messages: list[BufferedMessage]) -> list[str]:
        seen: list[str] = []
        for m in messages:
            title = self.members.get(m.sender)
            if title and title not in seen:
                seen.append(title)
        return seen

    def wikilink_aliases(self, messages: list[BufferedMessage]) -> dict[str, str]:
        """Surface forms that should become `[[title]]` in wiki prose:
        the member title, the WhatsApp display name, and the
        `Name (jid)` line the prompt showed the model."""
        aliases: dict[str, str] = {}
        for message in messages:
            title = self.members.get(message.sender)
            if not title:
                continue
            aliases[title] = title
            if message.sender_name and message.sender_name.strip():
                aliases[message.sender_name.strip()] = title
            shown = self.prompt_name(message)
            if shown != title:
                aliases[shown] = title
        return aliases


@dataclass
class AssignmentResult:
    buckets: dict[str, list[BufferedMessage]] = field(default_factory=dict)
    new_threads: dict[str, NewThread] = field(default_factory=dict)
    model_calls: int = 0
    unassigned: int = 0
