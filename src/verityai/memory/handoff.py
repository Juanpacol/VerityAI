"""Generating the structured handoff document.

The handoff is the harness's most testable claim: paste it into a cold session
and the work continues without re-reading the repository. Everything else here
is infrastructure for producing this one artifact well.

Section order is fixed and deliberate — TASK, CURRENT STATE, DECISIONS,
CONSTRAINTS, DISCOVERIES, FAILURES, RELEVANT FILES, NEXT ACTION. It runs from
orientation to action, so a reader who stops halfway still has the frame, and
one who reads only the end knows what to do. NEXT ACTION is last because it is
what the reader acts on immediately and the end of a document is the second
strongest position in it.

When the budget forces cuts, sections degrade in a fixed order rather than
being truncated uniformly. Losing the tail of every section produces a
document that is uniformly useless; dropping whole low-priority sections
produces a shorter document that still works. FAILURES and CONSTRAINTS are cut
last among the droppable ones — a repeated dead end is expensive, and a
violated hard constraint invalidates the work outright.
"""

from verityai.context.tokenizer import TokenCounter
from verityai.core.models import (
    Constraint,
    Decision,
    Discovery,
    Failure,
    Surfacing,
    Task,
)
from verityai.memory.store import MemoryStore

# Cut order when over budget: first entry is sacrificed first. TASK and
# NEXT ACTION are absent — a handoff without them is not a handoff, so if the
# budget cannot fit those two, the caller gets an over-budget document and is
# told, rather than a document that has quietly stopped being one.
_CUT_ORDER = ("discoveries", "decisions_rationale", "failures", "constraints", "decisions")


def build_handoff(
    store: MemoryStore,
    budget: int | None = None,
    counter: TokenCounter | None = None,
) -> tuple[str, dict]:
    """Build the handoff document from persisted state.

    Args:
        store: Where the task state lives.
        budget: Optional token ceiling. Sections are dropped in `_CUT_ORDER`
            until the document fits.
        counter: Token counter; one is constructed if not supplied.

    Returns:
        `(markdown, report)`. The report records the token count, the counting
        method, and exactly which sections were dropped — a caller must be
        able to tell a complete handoff from a degraded one without diffing
        it against the state.
    """
    counter = counter or TokenCounter()

    task = store.task()
    decisions = store.decisions()
    constraints = store.constraints()
    discoveries = store.discoveries()
    failures = store.failures(include_resolved=False)

    dropped: list[str] = []
    include_rationale = True

    document = _render(task, decisions, constraints, discoveries, failures, include_rationale)
    count = counter.count(document)

    if budget is not None:
        for cut in _CUT_ORDER:
            if count.tokens <= budget:
                break

            if cut == "discoveries":
                discoveries = []
            elif cut == "decisions_rationale":
                include_rationale = False
            elif cut == "failures":
                failures = []
            elif cut == "constraints":
                constraints = []
            elif cut == "decisions":
                decisions = []

            dropped.append(cut)
            document = _render(
                task, decisions, constraints, discoveries, failures, include_rationale
            )
            count = counter.count(document)

    budget_met = budget is None or count.tokens <= budget

    # Phase 2 (ADR-0023): the one place this project could answer "was this
    # memory surfaced, and when" and never did. Everything needed is already
    # in scope at this point -- the post-cut record ids, what was dropped to
    # fit, and the budget outcome -- so this costs nothing extra to compute.
    # `used` stays unset: whether the reader acted on it is not observable
    # from here.
    surfaced_ids = [r.id for r in (*decisions, *constraints, *discoveries, *failures)]
    store.append(
        Surfacing(
            surfaced_via="handoff",
            record_ids=surfaced_ids,
            dropped=list(dropped),
            budget_met=budget_met,
            token_count=count.tokens,
            token_method=count.method,
            source="memory.handoff",
        )
    )

    return document, {
        "tokens": count.tokens,
        "token_method": count.method,
        "budget": budget,
        "budget_met": budget_met,
        "dropped_sections": dropped,
        "counts": {
            "decisions": len(decisions),
            "constraints": len(constraints),
            "discoveries": len(discoveries),
            "failures": len(failures),
        },
    }


def render_token_footer(report: dict) -> str:
    """The token-count line `build_handoff` callers print, plus dropped
    sections if any -- one string, so the CLI and MCP surfaces cannot drift
    on wording the way they had (same data, different padding) before this
    was pulled out.

    Returns the footer only; where a caller puts it (stdout, stderr, appended
    to a returned string) stays theirs to decide.
    """
    lines = [f"[{report['tokens']:,} tokens, {report['token_method']}]"]
    if report["dropped_sections"]:
        lines.append(f"[dropped to fit budget: {', '.join(report['dropped_sections'])}]")
    return "\n".join(lines)


def _render(
    task: Task | None,
    decisions: list[Decision],
    constraints: list[Constraint],
    discoveries: list[Discovery],
    failures: list[Failure],
    include_rationale: bool,
) -> str:
    """Render the document. Empty sections are stated, not omitted.

    "DECISIONS: none recorded" tells the reader that nothing was decided.
    A missing section tells them nothing, and invites them to assume the
    harness lost it — which is exactly the doubt this document exists to
    remove.
    """
    lines: list[str] = ["# HANDOFF", ""]

    lines.append("## TASK")
    if task is None:
        lines.append('No task recorded. Run `verity task set "<title>"` to start one.')
    else:
        lines.append(f"**{task.title}**")
        if task.description:
            lines.extend(["", task.description])
    lines.append("")

    lines.append("## CURRENT STATE")
    if task is None:
        lines.append("Unknown — no task recorded.")
    else:
        lines.append(f"Status: {task.status}")
        lines.append(f"Last updated: {task.updated_at.isoformat()}")
    lines.append("")

    lines.append("## DECISIONS")
    if not decisions:
        lines.append("None recorded.")
    else:
        for decision in decisions:
            lines.append(f"- {decision.statement}")
            if include_rationale and decision.rationale:
                lines.append(f"  - *why:* {decision.rationale}")
    lines.append("")

    lines.append("## CONSTRAINTS")
    if not constraints:
        lines.append("None recorded.")
    else:
        for constraint in constraints:
            marker = "**[HARD]**" if constraint.hard else "[soft]"
            lines.append(f"- {marker} {constraint.statement}")
    lines.append("")

    lines.append("## DISCOVERIES")
    if not discoveries:
        lines.append("None recorded.")
    else:
        for discovery in discoveries:
            lines.append(f"- {discovery.statement}")
    lines.append("")

    lines.append("## FAILURES")
    if not failures:
        lines.append("None recorded.")
    else:
        lines.append("Already tried and did not work — do not repeat:")
        for failure in failures:
            lines.append(f"- {failure.attempted}")
            if failure.error:
                lines.append(f"  - *error:* {failure.error}")
    lines.append("")

    lines.append("## RELEVANT FILES")
    if task is None or not task.relevant_files:
        lines.append("None recorded.")
    else:
        for path in task.relevant_files:
            lines.append(f"- `{path}`")
    lines.append("")

    lines.append("## NEXT ACTION")
    if task is None or not task.next_action:
        lines.append("Not recorded — decide this before continuing.")
    else:
        lines.append(task.next_action)
    lines.append("")

    return "\n".join(lines)
