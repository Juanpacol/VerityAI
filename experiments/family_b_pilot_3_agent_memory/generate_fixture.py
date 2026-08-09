"""Generates pilot 3's fixture, deterministically.

Run once (`python3 generate_fixture.py`) to (re)produce `raw_log.json` and
`ground_truth.json`.

Design: unlike pilot 2 (a single-shot recall from a harness-prepared
context), this fixture is consumed one turn at a time by a *fresh* agent
invocation per turn -- no shared conversation memory between turns. That is
the mechanism, not an artifact: a new agent call genuinely has no memory of
the previous one, which is a real, not simulated, sliding context window.
Whatever a turn's agent does not persist outside that turn is gone by the
next one, in both conditions.

Turn 1 states the target figure once, in plain prose, with no
`verity:critical` marker. Turn 2 is pure filler. Turn 3 introduces a decoy
(a different, similarly-formatted figure attributed to a closed, unrelated
case) so a naive answer isn't simply "the only number available at the end".
Turn 4 asks the final question. Only an agent that proactively persisted the
turn-1 figure through some means outside its own turn (the `verity`
condition's memory tool) can possibly answer turn 4 correctly.

Deliberately no IBAN-shaped account number here (unlike pilot 2): an earlier
draft used one and it got auto-redacted or outright blocked by this
environment's own safety classifier when an agent tried to persist it via
`verity remember` -- the classifier pattern-matches IBAN-shaped strings as
credentials. That is a real, useful finding about operating a memory tool
under a safety layer, but it is not what this pilot means to measure, so
the target figure here is a plain currency amount only -- still enough to
trigger the ADR-0012 classification rule (currency symbol required), and
not shaped like a secret.
"""

import json
from pathlib import Path

GROUND_TRUTH = {
    "total_amount": "$4,231.50",
}

DECOY = {
    "total_amount": "$500.00",
}


def build_turns() -> list[dict]:
    return [
        {
            "turn": 1,
            "window": (
                "You are a support assistant reviewing customer billing case #7788, "
                "across a multi-step session. This is turn 1 of 4.\n\n"
                f"Looking up case #7788: the total amount currently owed is "
                f"{GROUND_TRUTH['total_amount']}.\n\n"
                "Acknowledge this and continue -- there is nothing else to do yet."
            ),
        },
        {
            "turn": 2,
            "window": (
                "This is turn 2 of 4 of the same session (case #7788). "
                "You have no memory of turn 1 beyond what you can recover yourself.\n\n"
                "[ticket-system] sync: status=open, queue=billing, "
                "last_touched_by=agent_2, sla_minutes_remaining=94\n\n"
                "Nothing to action here. Continue."
            ),
        },
        {
            "turn": 3,
            "window": (
                "This is turn 3 of 4 of the same session (case #7788). "
                "You have no memory of turns 1-2 beyond what you can recover yourself.\n\n"
                f"For reference, in the previous case #4521 the amount owed was "
                f"{DECOY['total_amount']}. That case is already closed and unrelated "
                "to #7788.\n\n"
                "Nothing to action here. Continue."
            ),
        },
        {
            "turn": 4,
            "window": (
                "This is turn 4 of 4, the final turn of the same session (case #7788). "
                "You have no memory of turns 1-3 beyond what you can recover yourself.\n\n"
                "Before you hand this off: what is the exact total amount owed for the "
                "CURRENT case (#7788) -- not any previously referenced case? If you do "
                "not know, say exactly 'INSUFFICIENT INFORMATION'. Do not guess."
            ),
        },
    ]


def main() -> None:
    out_dir = Path(__file__).parent
    turns = build_turns()

    (out_dir / "raw_log.json").write_text(json.dumps(turns, indent=2), encoding="utf-8")
    (out_dir / "ground_truth.json").write_text(
        json.dumps({"ground_truth": GROUND_TRUTH, "decoy": DECOY}, indent=2), encoding="utf-8"
    )
    print(f"Wrote {len(turns)} turns to raw_log.json")


if __name__ == "__main__":
    main()
