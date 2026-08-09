"""Prepares the two contexts each pilot trial receives, deterministically.

Run once per chosen `--budget` to (re)produce `naive_context.txt` and
`verity_context.txt` from `raw_log.json`. Both are committed, so a trial is
reproducible without re-running this script.

`naive` simulates the realistic failure mode of an unmanaged context
window: when it fills up, most systems keep the *most recent* messages and
drop the oldest ones (a user is usually continuing forward, not backward).
That is exactly the shape that endangers this fixture's target figure --
mentioned once, early (message #3 of 45) -- while very likely keeping the
decoy figure, which sits past the midpoint. This is deliberately different from "give the naive condition the raw log
unmanaged": an agent that can just read the whole raw file would trivially
tie its Verity counterpart on recall, since pruning only ever removes
information. The naive condition here is genuinely information-poorer,
matching what happens when a real context window overflows without any
tool managing it.
"""

import argparse
import json
from pathlib import Path

from verityai.context.ingest import load
from verityai.context.prune import ContextPipeline
from verityai.context.tokenizer import TokenCounter


def naive_tail_truncate(messages: list[dict], budget: int, counter: TokenCounter) -> str:
    """Keep the most recent messages that fit in `budget` tokens.

    Walks from the end backward, exactly what an unmanaged sliding window
    does when new messages arrive and old ones are evicted to make room.
    """
    kept: list[dict] = []
    used = 0
    for message in reversed(messages):
        cost = counter.count(json.dumps(message)).tokens
        if used + cost > budget and kept:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    return "\n\n".join(f"[{m['role']}] {m['content']}" for m in kept)


def verity_prepare(raw_log_path: Path, budget: int, task: str, counter: TokenCounter) -> str:
    """Run the real Verity pipeline at the same budget."""
    raw = raw_log_path.read_text(encoding="utf-8")
    items = load(raw)
    result = ContextPipeline(counter=counter).run(items, task=task, budget=budget)
    return "\n\n".join(f"[{item.kind.value}] {item.content}" for item in result.items)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument(
        "--task", default="find the current case's account number and amount owed"
    )
    args = parser.parse_args()

    out_dir = Path(__file__).parent
    raw_log_path = out_dir / "raw_log.json"
    messages = json.loads(raw_log_path.read_text(encoding="utf-8"))
    counter = TokenCounter()

    naive_text = naive_tail_truncate(messages, args.budget, counter)
    verity_text = verity_prepare(raw_log_path, args.budget, args.task, counter)

    (out_dir / "naive_context.txt").write_text(naive_text, encoding="utf-8")
    (out_dir / "verity_context.txt").write_text(verity_text, encoding="utf-8")

    print(f"budget={args.budget}")
    print(f"naive_context.txt:  {counter.count(naive_text).tokens} tokens")
    print(f"verity_context.txt: {counter.count(verity_text).tokens} tokens")


if __name__ == "__main__":
    main()
