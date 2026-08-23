"""Runs both arms (naive tail-truncation vs `verity context`) over the three
fixtures in logs/, at a 25% token budget, and writes retained evidence.

No agent in the loop -- both arms are deterministic functions of the fixture,
so this script alone re-derives the whole result (CLAUDE.md invariant 7).
Re-run it and `evidence/manifest.jsonl` reproduces byte-for-byte.

Usage: python3 run_pilot9.py
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

from verityai.context.ingest import load  # noqa: E402
from verityai.context.prune import ContextPipeline  # noqa: E402
from verityai.context.tokenizer import TokenCounter  # noqa: E402

TASKS = {
    "auth_service": "find the exact bug in refresh_session and its fix",
    "billing_service": "find the exact bug in prorate_charge and its fix",
    "search_service": "find the exact bug in reindex_document and its fix",
}

SIGNAL_MARKERS = {
    "auth_service": "This is the actual bug",
    "billing_service": "This is the actual bug",
    "search_service": "This is the actual bug",
}

BUDGET_FRACTION = 0.25


def sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def naive_tail_truncate(raw_messages: list[dict], budget: int, counter: TokenCounter) -> str:
    """Fill the budget from the end of the conversation backward -- the
    obvious baseline anyone would reach for without a pruning pipeline."""
    kept = []
    total = 0
    for msg in reversed(raw_messages):
        t = counter.count(msg["content"]).tokens
        if total + t > budget:
            break
        kept.append(msg)
        total += t
    kept.reverse()
    return "\n\n".join(m["content"] for m in kept)


def verity_context(raw: str, task: str, budget: int) -> str:
    counter = TokenCounter()
    pipeline = ContextPipeline(counter=counter)
    items = load(raw)
    result = pipeline.run(items, task=task, budget=budget)
    return "\n\n".join(i.content for i in result.items)


def main():
    logs_dir = HERE / "logs"
    evidence_dir = HERE / "evidence"
    trials_dir = evidence_dir / "trials"
    trials_dir.mkdir(parents=True, exist_ok=True)

    counter = TokenCounter()
    manifest = []
    report_rows = []

    for name in sorted(TASKS):
        log_path = logs_dir / f"{name}.json"
        raw = log_path.read_text()
        raw_messages = json.loads(raw)
        total_tokens = sum(counter.count(m["content"]).tokens for m in raw_messages)
        budget = int(total_tokens * BUDGET_FRACTION)
        task = TASKS[name]
        marker = SIGNAL_MARKERS[name]

        naive_out = naive_tail_truncate(raw_messages, budget, counter)
        verity_out = verity_context(raw, task, budget)

        for condition, output in (("naive", naive_out), ("verity", verity_out)):
            trial_dir = trials_dir / f"{name}_{condition}"
            trial_dir.mkdir(exist_ok=True)
            (trial_dir / "output.txt").write_text(output)

            signal_survives = marker.lower() in output.lower()
            manifest.append(
                {
                    "trial_id": f"{name}_{condition}",
                    "fixture": name,
                    "condition": condition,
                    "budget": budget,
                    "total_tokens": total_tokens,
                    "signal_marker": marker,
                    "signal_survives": signal_survives,
                    "fixture_hash": sha256(raw),
                    "output_hash": sha256(output),
                    "output_path": f"trials/{name}_{condition}/output.txt",
                }
            )
            report_rows.append(
                {"fixture": name, "condition": condition, "signal_survives": signal_survives}
            )
            print(f"{name}/{condition}: budget={budget} signal_survives={signal_survives}")

    (evidence_dir / "manifest.jsonl").write_text(
        "\n".join(json.dumps(m) for m in manifest) + "\n"
    )

    naive_wins = sum(1 for r in report_rows if r["condition"] == "naive" and r["signal_survives"])
    verity_wins = sum(1 for r in report_rows if r["condition"] == "verity" and r["signal_survives"])
    n_fixtures = len(TASKS)

    report = {
        "spec_name": "family_b_pilot_9_long_log_recovery",
        "budget_fraction": BUDGET_FRACTION,
        "n_fixtures": n_fixtures,
        "naive_signal_survives": f"{naive_wins}/{n_fixtures}",
        "verity_signal_survives": f"{verity_wins}/{n_fixtures}",
        "conclusion": (
            "verity recovers the buried signal in every fixture where naive tail-truncation "
            "loses it"
            if verity_wins == n_fixtures and naive_wins == 0
            else "mixed -- see manifest.jsonl for per-trial detail"
        ),
        "rows": report_rows,
    }
    (evidence_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    print(f"\nnaive: {naive_wins}/{n_fixtures}  verity: {verity_wins}/{n_fixtures}")
    print(f"Evidence written to {evidence_dir}")


if __name__ == "__main__":
    main()
