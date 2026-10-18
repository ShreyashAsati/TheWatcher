---
type: note
title: ADR 0016 — Fan-out and scoped direct agent runs
status: accepted
tags:
  - watcher
  - architecture
---
# ADR 0016 — Fan-out and scoped direct agent runs

## Decision

After each WhatsApp thread revision, the Worker may return source-grounded
fan-out candidates. The application validates every source id and destination
before delivery. Project context is posted to `inbox/project_agent.md`, and
research context is posted to `inbox/research_agent.md` while the Research
Agent remains frozen by ADR-0010.

Member facts use a separate durable confirmation workflow. A candidate is
sent to the linked member in a private message and is written to the member's
`Facts` section only after an `OK` reply or any reaction to that confirmation.
Temporary facts retain their validity date; historical facts are dated rather
than silently removed.

The Chat Agent may read the wiki for any directly addressed request. It may
delegate a read-only project or event question to the Project Agent, whose
read path can use the wider project/event pages rather than only the current
channel. An immediate project or event write uses a separate direct-run
method, does not consume the corresponding Inbox, and is allowed only for a
Bot Admin or the initiative lead.

## Consequences

- Existing thread pages, chatter handling, scheduler locks, and normal Project
  Agent inbox runs remain unchanged.
- Ordinary WhatsApp DMs remain rejected. Only a sender with an active member
  confirmation is admitted to the scoped DM hooks.
- Fan-out delivery is best effort during ingestion and idempotent at the
  destination. A failed confirmation or inbox write does not mark a message
  as a second source of truth.
