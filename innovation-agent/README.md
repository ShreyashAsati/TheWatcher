# Innovation Agent — MVP scaffold

Matches the design: three lanes (grounded / bridged / free) all query one
`MemoryInterface`, every pitch passes through one shared `gate`
(redundancy → relevance → novelty), survivors go to a human, outcomes get
written back to memory. The Project Agent's job (checking ongoing
projects for overlap) is deliberately **not** in here.

## Files

- `schemas.py` — `Idea`, `Evidence`, `Pitch`. Plain dataclasses, no ORM.
- `memory_interface.py` — the abstract `MemoryInterface`, a `MockMemory`
  with hand-written sample data (for the demo), and `LapisAdapter`, a
  real-but-guessed implementation against an assumed Obsidian-style
  vault layout (see the docstring). **This is the one file to rewrite
  once Lapis's actual file/API shape is confirmed — nothing else
  changes**, since lanes/gate/pipeline only ever call `MemoryInterface`
  methods.
- `llm_client.py` — `generate_ideas()`. Falls back to a stub if
  `ANTHROPIC_API_KEY` isn't set, so the demo runs with no credentials.
  Swap `_call_anthropic` for a structured-output/tool-use call to get
  real `Idea` objects back instead of parsed prose.
- `lanes.py` — `grounded_on_closure`, `grounded_weekly_sweep`,
  `bridged_on_research`, `free_weekly`. Each just builds a prompt from
  memory and calls the LLM client.
- `gate.py` — `run_gate()`: redundancy → relevance → novelty, in that
  order, with lane-aware thresholds inside each check.
- `pipeline.py` — wires triggers to lanes to gate to memory. The only
  file that touches everything else.
- `demo.py` — runs all three lanes against `MockMemory` and prints the
  result.

## Run it

```bash
pip install pyyaml          # optional, only needed for LapisAdapter
python demo.py               # runs with stubbed LLM output
ANTHROPIC_API_KEY=sk-... python demo.py   # runs with real generation
```

## What's genuinely unfinished (marked `TODO` in code)

- Embedding-based similarity in `gate.py` (redundancy) and
  `memory_interface.py` (`get_capabilities_far_from`) — both use crude
  word-overlap placeholders right now.
- Structured-output parsing in `llm_client.py` — currently returns raw
  text in `Idea.statement` instead of a fully populated `Idea`.
- The evidence-verification loop (checking that cited `source_ids`
  actually back the claim) isn't built yet — `gate.py` only checks that
  evidence is *present*, not that it's *true*.
- `LapisAdapter`'s frontmatter field names (`status`, `cause`,
  `technologies`, `depth`, `topic`, `added_at`) are reasonable guesses,
  not confirmed against the real vault.
