# Project Agent

v1 scope only: consumes its Inbox, upserts Items into project and event
initiative pages, rewrites `## Status`, and answers explicitly delegated wiki
queries. Confirmed Proposals are executed by the WhatsApp Agent, not here.
Health score, nudges, critical analysis and GitHub sync
are explicitly out of v1 scope — see
[`docs/Spec - Watcher v1.md`](../../docs/Spec%20-%20Watcher%20v1.md)
("Project Agent v1" / "Out of Scope (v1)") and
[`docs/Plan - Watcher v1.md`](../../docs/Plan%20-%20Watcher%20v1.md)
(Phase 5).

## Layout

- `interface.py` — public surface: `run_project_agent_once`,
  `run_project_agent_direct`, `apply_thread_items_to_initiative`,
  `rewrite_status`.
- `agent.py` — `ProjectAgent` (inbox consumption) and one handler per
  item kind (task / decision / resource / question). `thread_update` and
  `project_context` items are applied to both project and event pages. The
  direct path reads the wider project/event wiki and performs writes only for
  a Bot Admin or the linked initiative lead; it never consumes the Inbox.
  `_merge_item` stays a pure policy function (`merge_item`).
