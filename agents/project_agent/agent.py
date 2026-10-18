"""Project Agent: consume inbox notices, carry thread items onto the
initiative page, rewrite Status.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from shared.cms.interface import default_cms_client
from shared.gateway.interface import get_channel, is_bot_admin, resolve_sender
from shared.inbox.interface import THREAD_UPDATE, Inbox, InboxItem
from shared.models.interface import TextModelClient, generate
from shared.observability.interface import PROJECT_AGENT, scope
from shared.wiki.interface import (
    Item,
    ThreadStore,
    VaultClient,
    append_items,
    append_lines,
    apply_member_wikilinks,
    dump_page,
    fenced_content,
    initiative_page_path,
    parse_items,
    parse_page,
    read_if_exists,
    set_fenced,
    upsert_items,
)

_WIKILINK_TITLE_RE = re.compile(r"\[\[([^\]|#]+)")

logger = logging.getLogger(__name__)

AGENT = "project_agent"
PROJECT_CONTEXT = "project_context"
PROJECT_REQUEST = "project_request"
_TASK_SECTION_BY_TYPE = {"project": "Open tasks", "event": "Logistics"}
_DIRECT_CONTEXT_LIMIT = 24_000


@dataclass
class ApplyResult:
    tasks_upserted: int = 0
    decisions_appended: int = 0
    resources_appended: int = 0
    log_lines_appended: int = 0
    skipped_no_target_section: list[str] = field(default_factory=list)


def merge_item(existing: Item, incoming: Item) -> Item:
    """A human-ticked box never gets unticked; a human-set owner/due survives."""
    checked = existing.checked if existing.checked else incoming.checked
    fields = dict(incoming.fields)
    for key in ("due", "owner"):
        if key in existing.fields:
            fields[key] = existing.fields[key]
    return Item(text=incoming.text, block_id=incoming.block_id, checked=checked, fields=fields)


def _grown(before, after, title: str) -> int:
    return len(parse_items(after.section(title).body)) - len(parse_items(before.section(title).body))


def _src(item: Item) -> dict[str, str]:
    return {"src": ", ".join(item.src_ids())}


def apply_task_items(page, items: list[Item], result: ApplyResult):
    task_section = _TASK_SECTION_BY_TYPE.get(page.page_type)
    if task_section and page.section(task_section) is not None:
        page = upsert_items(page, task_section, items, merge=merge_item)
        result.tasks_upserted = len(items)
        return page
    result.skipped_no_target_section.append("task")
    return page


def apply_decision_items(page, items: list[Item], result: ApplyResult):
    mapped = [Item(i.text, i.block_id, None, {"by": "system", **_src(i)}) for i in items]
    before = page
    page = append_items(page, "Decisions", mapped)
    result.decisions_appended = _grown(before, page, "Decisions")
    return page


def apply_resource_items(page, items: list[Item], result: ApplyResult):
    mapped = [Item(i.text, i.block_id, None, _src(i)) for i in items]
    before = page
    page = append_items(page, "Resources", mapped)
    result.resources_appended = _grown(before, page, "Resources")
    return page


def apply_question_items(page, items: list[Item], result: ApplyResult):
    mapped = [Item(f"Open question: {i.text}", i.block_id, None, _src(i)) for i in items]
    before = page
    page = append_items(page, "Log", mapped)
    result.log_lines_appended = _grown(before, page, "Log")
    return page


_KIND_HANDLERS = {
    "task": apply_task_items,
    "decision": apply_decision_items,
    "resource": apply_resource_items,
    "question": apply_question_items,
}


async def apply_thread_items_to_initiative(
    vault: VaultClient, thread_path: str, initiative_path: str
) -> ApplyResult:
    result = ApplyResult()
    thread_page = parse_page((await vault.read(thread_path)).content)
    incoming = parse_items(fenced_content(thread_page, "Items"))
    if not incoming:
        return result

    initiative = await vault.read(initiative_path)
    page = parse_page(initiative.content)

    by_kind: dict[str, list[Item]] = {}
    for item in incoming:
        by_kind.setdefault(item.fields.get("kind", "task"), []).append(item)

    for kind, items in by_kind.items():
        handler = _KIND_HANDLERS.get(kind)
        if handler is None:
            continue
        page = handler(page, items, result)

    new_content = dump_page(page)
    if new_content != initiative.content:
        await vault.write(initiative_path, new_content, base_revision=initiative.revision)
    return result


async def apply_context_to_initiative(
    vault: VaultClient, initiative_path: str, item: InboxItem
) -> ApplyResult:
    """Apply a source-grounded fan-out context line during the normal run."""
    result = ApplyResult()
    page_result = await vault.read(initiative_path)
    page = parse_page(page_result.content)
    source = ", ".join(item.source_ids) if item.source_ids else (item.since or "unknown")
    line = f"- Fan-out context: {item.text} [src:: {source}]"
    section = "Log" if page.page_type == "event" else "Notes"
    if page.section(section) is None:
        section = "Log" if page.section("Log") is not None else "Notes"
    if page.section(section) is not None:
        updated = append_lines(page, section, [line])
        if dump_page(updated) != page_result.content:
            await vault.write(initiative_path, dump_page(updated), base_revision=page_result.revision)
            result.log_lines_appended = 1
    return result


async def _member_aliases(page_text: str) -> dict[str, str]:
    """Titles already on the page, plus the ARIES roster, so a status
    line that says "Aira" is stored as [[Aira]]."""
    aliases = {
        title.strip(): title.strip() for title in _WIKILINK_TITLE_RE.findall(page_text) if title.strip()
    }
    try:
        for member in await default_cms_client().list_members():
            if member.title.strip():
                aliases[member.title.strip()] = member.title.strip()
    except Exception:  # noqa: BLE001 - the page's own wikilinks still apply
        logger.exception("project agent: member roster unavailable; linking names already on the page")
    return aliases


async def _direct_wiki_context(
    vault: VaultClient, current_path: str | None, current_content: str | None
) -> str:
    """Collect bounded project/event pages for a direct read query.

    The current initiative is first when available; the remaining pages give
    direct queries the cross-project context that the WhatsApp message window
    does not contain. The cap keeps the delegated prompt predictable.
    """
    paths: list[str] = []
    if current_path:
        paths.append(current_path)
    for prefix in ("projects", "events"):
        try:
            for path in await vault.list(prefix):
                if path not in paths:
                    paths.append(path)
        except Exception:  # noqa: BLE001 - a missing prefix is valid
            continue

    chunks: list[str] = []
    used = 0
    for path in paths:
        content = current_content if path == current_path else None
        if content is None:
            try:
                content = (await vault.read(path)).content
            except Exception:  # noqa: BLE001 - skip a page unavailable during this read
                continue
        remaining = _DIRECT_CONTEXT_LIMIT - used
        if remaining <= 0:
            break
        chunk = content[:remaining]
        chunks.append(f"## {path}\n{chunk}")
        used += len(chunk)
    return "\n\n".join(chunks)


async def rewrite_status(vault: VaultClient, initiative_path: str, worker_client: TextModelClient) -> None:
    result = await vault.read(initiative_path)
    page = parse_page(result.content)
    task_section_title = _TASK_SECTION_BY_TYPE.get(page.page_type)
    aliases = await _member_aliases(result.content)
    tasks_body = fenced_content(page, task_section_title) if task_section_title else ""
    decisions_body = fenced_content(page, "Decisions")
    tasks_body = apply_member_wikilinks(tasks_body, aliases)
    decisions_body = apply_member_wikilinks(decisions_body, aliases)

    prompt = (
        f"Open tasks:\n{tasks_body}\n\nRecent decisions:\n{decisions_body}\n\n"
        "Write a status update in at most 5 sentences. "
        "When you name a member, use their [[wikilink]] exactly as it appears above."
    )
    with scope(phase=PROJECT_AGENT):
        text_result = await generate(worker_client, "worker", prompt)
    status = apply_member_wikilinks(text_result.text.strip(), aliases)
    page = set_fenced(page, "Status", status)
    await vault.write(initiative_path, dump_page(page), base_revision=result.revision)


class ProjectAgent:
    def __init__(self, vault: VaultClient, worker_client: TextModelClient) -> None:
        self.vault = vault
        self.worker_client = worker_client
        self.inbox = Inbox(vault, AGENT)

    async def run_once(self) -> list[ApplyResult]:
        results: list[ApplyResult] = []
        done: list[str] = []
        for item in await self.inbox.pending():
            if item.kind not in {THREAD_UPDATE, PROJECT_CONTEXT}:
                logger.info(
                    "project agent: leaving %s item %s (%s) for a human", item.kind, item.id, item.text
                )
                continue
            try:
                result = await (
                    self._apply_update(item)
                    if item.kind == THREAD_UPDATE
                    else self._apply_context(item)
                )
            except Exception:  # noqa: BLE001 - one bad item must not block the rest
                logger.exception("project agent: applying inbox item %s failed; will retry", item.id)
                continue
            if result is None:
                # Keep malformed or not-yet-provisioned targets pending so a
                # later run can retry them after the corresponding page exists.
                continue
            results.append(result)
            done.append(item.id)
        await self.inbox.ack(done)
        return results

    async def _apply_update(self, item: InboxItem) -> ApplyResult | None:
        channel = await get_channel(item.channel) if item.channel else None
        if channel is None or not channel.initiative or not item.thread_slug:
            return None
        thread = await ThreadStore(self.vault, channel).get(item.thread_slug)
        if thread is None:
            return None
        initiative_path = initiative_page_path(channel.kind, channel.initiative)
        result = await apply_thread_items_to_initiative(self.vault, thread.path, initiative_path)
        await rewrite_status(self.vault, initiative_path, self.worker_client)
        return result

    async def _apply_context(self, item: InboxItem) -> ApplyResult | None:
        channel = await get_channel(item.channel) if item.channel else None
        title = item.target or (channel.initiative if channel else None)
        if title is None:
            return None
        kind = channel.kind if channel and channel.kind in {"project", "event"} else "project"
        path = initiative_page_path(kind, title)
        try:
            await self.vault.read(path)
        except Exception:  # noqa: BLE001 - unknown target remains pending for a human
            return None
        result = await apply_context_to_initiative(self.vault, path, item)
        await rewrite_status(self.vault, path, self.worker_client)
        return result

    async def run_direct(
        self, channel_jid: str, task: str, requester: str, *, write: bool = False
    ) -> str:
        """Answer a project query or perform an authorized immediate update.

        This method never reads or acknowledges the agent inbox. Direct writes
        are restricted to Bot Admins and the initiative lead.
        """
        channel = await get_channel(channel_jid)
        if not write:
            current_path = None
            current_content = None
            if channel is not None and channel.kind in {"project", "event"} and channel.initiative:
                current_path = initiative_page_path(channel.kind, channel.initiative)
                current = await read_if_exists(self.vault, current_path)
                if current is not None:
                    current_content = current.content
            context = await _direct_wiki_context(self.vault, current_path, current_content)
            if not context:
                return "I couldn't find any project or event pages in the wiki."
            prompt = (
                f"Project and event wiki context:\n{context}\n\n"
                f"Question:\n{task}\n\nAnswer only from the page, and say when it is not present."
            )
            with scope(phase=PROJECT_AGENT):
                answer = await generate(self.worker_client, "worker", prompt)
            return answer.text.strip()

        if channel is None or channel.kind not in {"project", "event"} or not channel.initiative:
            return "This channel is not linked to a project or event."
        path = initiative_page_path(channel.kind, channel.initiative)
        result = await self.vault.read(path)
        page = parse_page(result.content)

        authorised = await is_bot_admin(requester)
        if not authorised:
            linked = await resolve_sender(requester)
            lead = str(getattr(page.frontmatter, "lead", "")).strip("[]")
            authorised = linked is not None and linked.member_title == lead
        if not authorised:
            return "Project writes require a Bot Admin or the project lead."
        prompt = (
            f"Current project page:\n{result.content[:12000]}\n\n"
            f"Authorized update request:\n{task}\n\n"
            "Write a concise status update in at most 5 sentences. Preserve only facts in the "
            "request or current page and use member wikilinks."
        )
        with scope(phase=PROJECT_AGENT):
            generated = await generate(self.worker_client, "worker", prompt)
        aliases = await _member_aliases(result.content)
        updated = set_fenced(page, "Status", apply_member_wikilinks(generated.text.strip(), aliases))
        await self.vault.write(path, dump_page(updated), base_revision=result.revision)
        return f"Updated {channel.initiative} through the Project Agent."
