"""Generates the numeric-recall pilot's fixture, deterministically.

Run once (`python3 generate_fixture.py`) to (re)produce `raw_log.json` and
`ground_truth.json` from this script -- checked in alongside the script so
the fixture is reproducible without needing to re-run it, but changes to
the design happen here, not by hand-editing the generated JSON.

Design: a support-conversation log where the target account number and
amount are mentioned exactly once, early, in plain prose with no
`verity:critical` marker -- the whole point is testing *automatic*
protection (ADR-0012), not manual tagging. A decoy figure (a different,
similarly-formatted account/amount, attributed to a past case) appears
past the midpoint, so a naive recall is not simply "the only number in the
log." Between both, filler tool-output noise pads the log long enough that
a real token budget forces a real choice.
"""

import json
from pathlib import Path

GROUND_TRUTH = {
    "account_number": "DE89370400440532013000",
    "total_amount": "$4,231.50",
}

DECOY = {
    "account_number": "FR7630006000011234567890189",
    "total_amount": "$500.00",
}


def build_messages() -> list[dict]:
    messages = [
        {
            "role": "system",
            "content": "You are a support assistant reviewing a customer billing case.",
        },
        {
            "role": "user",
            "content": "Please review case #7788 and get it ready for the next agent.",
        },
        {
            "role": "assistant",
            "content": (
                f"Looking up case #7788 now. Customer's account number is "
                f"{GROUND_TRUTH['account_number']} and the total amount currently "
                f"owed is {GROUND_TRUTH['total_amount']}."
            ),
        },
    ]

    # Filler: routine tool-output noise, varied so dedup can't collapse it
    # to nothing on its own -- the pilot is about budget-driven dropping,
    # not about testing deduplication.
    for i in range(40):
        messages.append(
            {
                "role": "tool",
                "content": (
                    f"[ticket-system] sync #{i}: status=open, queue=billing, "
                    f"last_touched_by=agent_{i % 5}, sla_minutes_remaining={120 - i}"
                ),
            }
        )
        if i == 22:
            # The decoy: a different, similarly-formatted figure, attributed
            # to a past, unrelated case -- placed past the midpoint so a
            # naive tail-keep truncation (see README) is more likely to
            # retain the decoy than the real, early-mentioned figure.
            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        f"For reference, in the previous case #4521 the account was "
                        f"{DECOY['account_number']} with {DECOY['total_amount']} owed. "
                        "That case is already closed and unrelated to #7788."
                    ),
                }
            )

    messages.append(
        {
            "role": "user",
            "content": (
                "Before you hand this off: what is the exact account number and "
                "total amount owed for the CURRENT case (#7788) -- not any "
                "previously referenced case?"
            ),
        }
    )
    return messages


def main() -> None:
    out_dir = Path(__file__).parent
    messages = build_messages()

    (out_dir / "raw_log.json").write_text(json.dumps(messages, indent=2), encoding="utf-8")
    (out_dir / "ground_truth.json").write_text(
        json.dumps({"ground_truth": GROUND_TRUTH, "decoy": DECOY}, indent=2), encoding="utf-8"
    )
    print(f"Wrote {len(messages)} messages to raw_log.json")


if __name__ == "__main__":
    main()
