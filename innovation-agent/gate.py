"""
The shared gate every pitch passes through before a human ever sees it:
redundancy -> relevance -> novelty, in that order (cheapest and most
disqualifying first), with lane-aware thresholds baked into each check.
"""
from dataclasses import dataclass

from schemas import Idea, Origin, PitchStatus


@dataclass
class GateResult:
    passed: bool
    status: PitchStatus
    notes: list[str]


def _title_similarity(a: str, b: str) -> float:
    # TODO: replace with real embedding cosine similarity once retrieval
    # is wired up. Cheap placeholder: word-overlap ratio.
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def check_redundancy(idea: Idea, pitch_history: list[dict]) -> GateResult:
    for past in pitch_history:
        if past.get("outcome") == "reject" and _title_similarity(idea.title, past["title"]) > 0.6:
            return GateResult(False, PitchStatus.GATED_OUT, [f"too similar to rejected pitch '{past['title']}'"])
    return GateResult(True, PitchStatus.PENDING, [])


def check_relevance(idea: Idea, org_snapshot: str) -> GateResult:
    # Grounded ideas are relevant by construction — they came out of
    # ARIES's own memory. This check mostly does work for bridged/free.
    if idea.origin == Origin.GROUNDED:
        return GateResult(True, PitchStatus.PENDING, [])
    if not idea.statement.strip():
        return GateResult(False, PitchStatus.GATED_OUT, ["empty statement"])
    # TODO: real check — embedding similarity or an LLM call asking
    # whether idea.statement connects to anything in org_snapshot.
    return GateResult(True, PitchStatus.PENDING, [])


def check_novelty(idea: Idea) -> GateResult:
    if idea.origin == Origin.FREE:
        # Judge substance, not evidence — evidence is optional by design
        # for free-lane pitches, so don't penalize the lack of it here.
        if len(idea.statement.split()) < 8:
            return GateResult(False, PitchStatus.GATED_OUT, ["too thin to be substantive"])
        return GateResult(True, PitchStatus.PENDING, [])
    # Grounded/bridged: novelty is close to given by construction, but
    # catch the degenerate case of a statement with no evidence at all.
    if not idea.evidence:
        return GateResult(False, PitchStatus.GATED_OUT, ["no evidence attached for a grounded/bridged pitch"])
    return GateResult(True, PitchStatus.PENDING, [])


def run_gate(idea: Idea, pitch_history: list[dict], org_snapshot: str) -> GateResult:
    for check in (
        lambda: check_redundancy(idea, pitch_history),
        lambda: check_relevance(idea, org_snapshot),
        lambda: check_novelty(idea),
    ):
        result = check()
        if not result.passed:
            return result
    return GateResult(True, PitchStatus.PENDING, [])
