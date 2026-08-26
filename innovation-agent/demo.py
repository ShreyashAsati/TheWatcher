"""
Runnable demo: fires all three lanes against MockMemory and prints what
would land in a human's review queue.

Run:  python demo.py
With a real LLM: export ANTHROPIC_API_KEY=... first.
"""
from memory_interface import MockMemory
import pipeline


def main():
    memory = MockMemory()

    print("=== Project closure (proj-auth-b) -> grounded lane ===")
    for p in pipeline.handle_project_closed(memory, "proj-auth-b"):
        print(f"[{p.status.value}] {p.idea.title} :: {p.idea.statement}")
        if p.gate_notes:
            print(f"    gate notes: {p.gate_notes}")

    print("\n=== New research deep-dive (res-1) -> bridged lane ===")
    for p in pipeline.handle_research_deepdive(memory, "res-1"):
        print(f"[{p.status.value}] {p.idea.title} :: {p.idea.statement}")
        if p.gate_notes:
            print(f"    gate notes: {p.gate_notes}")

    print("\n=== Weekly sweep -> grounded (idle capability) + free ===")
    for p in pipeline.run_weekly_sweep(memory):
        print(f"[{p.status.value}] {p.idea.title} :: {p.idea.statement}")
        if p.gate_notes:
            print(f"    gate notes: {p.gate_notes}")


if __name__ == "__main__":
    main()
