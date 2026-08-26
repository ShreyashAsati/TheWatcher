"""
The single interface the Innovation Agent talks to. Everything it knows
comes through this — it never touches Lapis (or Postgres, or anything
else) directly.

Swap `LapisAdapter`'s internals for whatever Lapis's real file layout /
frontmatter fields turn out to be; nothing else in the agent needs to
change, because the rest of the codebase only ever calls MemoryInterface
methods.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional
import re

try:
    import yaml  # pyyaml — Obsidian-style frontmatter is YAML between --- lines
except ImportError:
    yaml = None


class MemoryInterface(ABC):
    """What the Innovation Agent is allowed to ask memory for."""

    # -- triggers --
    @abstractmethod
    def poll_new_project_closures(self, since: str) -> list[str]:
        """Project IDs closed since `since`."""

    @abstractmethod
    def poll_new_research_deepdives(self, since: str) -> list[str]:
        """Research entry IDs that got a full deep-dive since `since`."""

    # -- grounded --
    @abstractmethod
    def get_failure_cause(self, project_id: str) -> Optional[str]:
        ...

    @abstractmethod
    def get_all_closed_project_causes(self) -> dict[str, str]:
        """project_id -> failure_cause, for closed projects only."""

    @abstractmethod
    def get_capability_usage(self) -> dict[str, list[str]]:
        """capability/technology name -> list of project IDs that used it."""

    # -- bridged --
    @abstractmethod
    def get_research_entry(self, entry_id: str) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_capabilities_far_from(self, topic: str, k: int = 5) -> list[dict[str, Any]]:
        """Capabilities/past projects semantically distant from `topic`."""

    # -- free --
    @abstractmethod
    def get_org_snapshot(self) -> str:
        """Compact prose summary of ARIES's current shape."""

    @abstractmethod
    def get_light_research_digest(self, since: str) -> list[dict[str, Any]]:
        """Broadly-scanned (not necessarily deep-dived) research entries."""

    # -- gate --
    @abstractmethod
    def get_pitch_history(self) -> list[dict[str, Any]]:
        ...

    # -- writeback --
    @abstractmethod
    def write_pitch(self, pitch) -> None:
        ...

    @abstractmethod
    def write_outcome(self, pitch_id: str, outcome: str, note: str = "") -> None:
        ...


class MockMemory(MemoryInterface):
    """
    Hand-written fake data so the pipeline is runnable and testable
    before Lapis is wired in for real. See demo.py.
    """

    def __init__(self):
        self._closed_causes = {
            "proj-auth-a": "underestimated OAuth edge cases",
            "proj-auth-b": "underestimated OAuth edge cases",
        }
        self._capability_usage = {
            "OCR": ["proj-ocr-a"],
            "RAG": ["proj-rag-a", "proj-rag-b", "proj-rag-c", "proj-rag-d"],
            "WebRTC": ["proj-callgpt"],
        }
        self._research = {
            "res-1": {
                "id": "res-1",
                "title": "Weak supervision for document layout understanding",
                "topic": "document understanding",
            }
        }
        self._light_digest = [
            {"id": "res-2", "title": "Neuroevolution for legged locomotion in cluttered terrain"},
            {"id": "res-3", "title": "Self-play curricula for negotiation agents"},
        ]
        self._pitch_history: list[dict[str, Any]] = []

    def poll_new_project_closures(self, since: str) -> list[str]:
        return ["proj-auth-b"]

    def poll_new_research_deepdives(self, since: str) -> list[str]:
        return ["res-1"]

    def get_failure_cause(self, project_id: str) -> Optional[str]:
        return self._closed_causes.get(project_id)

    def get_all_closed_project_causes(self) -> dict[str, str]:
        return dict(self._closed_causes)

    def get_capability_usage(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self._capability_usage.items()}

    def get_research_entry(self, entry_id: str) -> dict[str, Any]:
        return self._research.get(entry_id, {})

    def get_capabilities_far_from(self, topic: str, k: int = 5) -> list[dict[str, Any]]:
        return [{"name": "neuroevolution (Darwin Bot)", "distance": "far"}]

    def get_org_snapshot(self) -> str:
        return "ARIES: ML/AI club, active in RAG, voice pipelines, neuroevolution, document OCR."

    def get_light_research_digest(self, since: str) -> list[dict[str, Any]]:
        return list(self._light_digest)

    def get_pitch_history(self) -> list[dict[str, Any]]:
        return list(self._pitch_history)

    def write_pitch(self, pitch) -> None:
        self._pitch_history.append({"id": pitch.id, "title": pitch.idea.title})

    def write_outcome(self, pitch_id: str, outcome: str, note: str = "") -> None:
        for p in self._pitch_history:
            if p["id"] == pitch_id:
                p["outcome"] = outcome
                p["note"] = note


class LapisAdapter(MemoryInterface):
    """
    Skeleton for the real backend: a directory of Obsidian-style markdown
    files with YAML frontmatter and [[wikilinks]].

    Assumed vault layout (adjust to match Lapis's actual schema — this
    part is a guess, everything else in the codebase is not):

        vault/projects/*.md   frontmatter: status: active|closed, cause: ..., technologies: [...]
        vault/research/*.md   frontmatter: depth: light|deep, topic: ..., added_at: ...
        vault/pitches/*.md    frontmatter: origin, status, created_at

    Every method here is a real starting implementation against that
    assumed layout, not a NotImplementedError placeholder — so once the
    vault layout is confirmed, this is a find-and-adjust job, not a
    rewrite.
    """

    def __init__(self, vault_path: str):
        self.vault = Path(vault_path)

    def _read_notes(self, subdir: str) -> list[dict[str, Any]]:
        notes = []
        folder = self.vault / subdir
        if not folder.exists():
            return notes
        for f in folder.glob("*.md"):
            frontmatter, body = self._split_frontmatter(f.read_text(encoding="utf-8"))
            frontmatter["_id"] = f.stem
            frontmatter["_body"] = body
            frontmatter["_links"] = re.findall(r"\[\[([^\]]+)\]\]", body)
            notes.append(frontmatter)
        return notes

    @staticmethod
    def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
        if yaml and text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                meta = yaml.safe_load(parts[1]) or {}
                return meta, parts[2]
        return {}, text

    def poll_new_project_closures(self, since: str) -> list[str]:
        return [
            n["_id"] for n in self._read_notes("projects")
            if n.get("status") == "closed" and n.get("closed_at", "") > since
        ]

    def poll_new_research_deepdives(self, since: str) -> list[str]:
        return [
            n["_id"] for n in self._read_notes("research")
            if n.get("depth") == "deep" and n.get("added_at", "") > since
        ]

    def get_failure_cause(self, project_id: str) -> Optional[str]:
        for n in self._read_notes("projects"):
            if n["_id"] == project_id:
                return n.get("cause")
        return None

    def get_all_closed_project_causes(self) -> dict[str, str]:
        return {
            n["_id"]: n["cause"]
            for n in self._read_notes("projects")
            if n.get("status") == "closed" and n.get("cause")
        }

    def get_capability_usage_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for n in self._read_notes("projects"):
            for tech in n.get("technologies", []):
                counts[tech] = counts.get(tech, 0) + 1
        return counts

    def get_research_entry(self, entry_id: str) -> dict[str, Any]:
        for n in self._read_notes("research"):
            if n["_id"] == entry_id:
                return n
        return {}

    def get_capabilities_far_from(self, topic: str, k: int = 5) -> list[dict[str, Any]]:
        # TODO: real embedding-based distance (pgvector, or embed the
        # note bodies directly). For now: everything not tagged with
        # `topic` counts as "far", capped at k.
        return [n for n in self._read_notes("projects") if topic not in n.get("tags", [])][:k]

    def get_org_snapshot(self) -> str:
        active = [n["_id"] for n in self._read_notes("projects") if n.get("status") == "active"]
        return f"Active projects: {', '.join(active) if active else 'none'}."

    def get_light_research_digest(self, since: str) -> list[dict[str, Any]]:
        return [n for n in self._read_notes("research") if n.get("added_at", "") > since]

    def get_pitch_history(self) -> list[dict[str, Any]]:
        return self._read_notes("pitches")

    def write_pitch(self, pitch) -> None:
        folder = self.vault / "pitches"
        folder.mkdir(parents=True, exist_ok=True)
        fm = {
            "type": "pitch",
            "origin": pitch.idea.origin.value,
            "status": pitch.status.value,
            "created_at": pitch.created_at.isoformat(),
        }
        body = f"# {pitch.idea.title}\n\n{pitch.idea.statement}\n"
        header = yaml.safe_dump(fm) if yaml else str(fm)
        (folder / f"{pitch.id}.md").write_text(f"---\n{header}---\n{body}", encoding="utf-8")

    def write_outcome(self, pitch_id: str, outcome: str, note: str = "") -> None:
        f = self.vault / "pitches" / f"{pitch_id}.md"
        if not f.exists():
            return
        fm, body = self._split_frontmatter(f.read_text(encoding="utf-8"))
        fm["status"] = outcome
        fm["outcome_note"] = note
        header = yaml.safe_dump(fm) if yaml else str(fm)
        f.write_text(f"---\n{header}---\n{body}", encoding="utf-8")
