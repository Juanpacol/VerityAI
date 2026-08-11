"""MCP server: the same harness core, exposed to an agent instead of a human.

Every tool here is a thin wrapper over a function the CLI also calls. That is
the design constraint, not an accident — when an agent gets a surprising
context back, a person must be able to reproduce it with `verity context` and
see the same thing. A tool with logic of its own would break that. The bodies
live in `handlers.py`; this module is the surface, and the surface is the
descriptions.

There are five tools, not twenty-one, and the reason is measurable rather than
aesthetic: a client picks a tool by matching a request against descriptions, so
twenty-one entries spend the context budget that should have gone to *when to
call this* — and several of the twenty-one were the same function under two
names. Each tool now takes an `op`, and each op keeps the guidance the separate
tool used to carry (ADR-0030).

What this integration can and cannot do, stated plainly because it bounds
every claim the project can make:

- Verity **cannot see the agent's real context window.** MCP is a cooperative
  protocol; the agent calls a tool and gets an answer. There is no interception
  point, so "we pruned your context" is only true of context the agent chose to
  hand over.
- Verity **can** hold state the agent would otherwise lose, and hand back a
  reconstruction after a reset. That is genuinely useful and does not require
  seeing the window.

Tool descriptions are written for a model to read. They say when to call the
tool, not just what it does, because a tool an agent never thinks to call is
worth nothing.
"""

import argparse
import os
from typing import Literal

from verityai.mcp import handlers

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - import guard
    FastMCP = None  # type: ignore[assignment, misc]


def _missing(call: str, **named: object) -> str | None:
    """Name the argument an op needs, as an answer rather than an exception.

    A returned string is a better affordance for an agent than a transport
    error: it arrives where the agent is already reading, and it says which
    argument to add. `call` is the call it was reaching for, spelled out, so
    the answer doubles as the corrected example.
    """
    absent = [name for name, value in named.items() if not value]
    if not absent:
        return None
    which = " and ".join(f"`{name}`" for name in absent)
    return f"{call} needs {which}. Add it and call again."


def build_server(name: str = "verity", root: str | None = None):
    """Construct the MCP server.

    A factory rather than a module-level singleton so tests can build an
    isolated instance, and so importing this module never starts anything.

    `root` is where `.verity/` lives. When it is None the store is discovered
    from the process's working directory — fine when the client launches the
    server inside the project, wrong and silent when it does not, which is why
    `verity-mcp --root` exists.
    """
    if FastMCP is None:
        raise RuntimeError(
            "The MCP SDK is not installed. Install it with: pip install 'verityai[mcp]'"
        )

    server = FastMCP(name)

    @server.tool()
    def context(
        op: Literal["optimize", "health", "recall"],
        transcript: str = "",
        task: str = "",
        budget: int = 20000,
        context_sample: str = "",
    ) -> str:
        """Work on the conversation itself: prune it, measure it, or reload it.

        Call this when a session has been running for a while — when tool
        output has filled the window, when you are about to hand work over, or
        when you notice yourself re-deriving something.

        ops:
          optimize  Prune a context down to a token budget, keeping what
                    matters. Call this when the transcript has grown long or
                    before handing work to another agent. Returns the pruned
                    context plus a stage-by-stage ledger of what went and why;
                    items marked critical are never dropped, even if that means
                    exceeding the budget. Needs `transcript` (JSON messages or
                    plain text); takes `task`, `budget`.
          health    Assess the quality of a context, not just how full it is.
                    Call this to find out whether the context is still good —
                    high redundancy, heavy tool noise or low relevance density
                    all mean it is time to prune or hand off. Reports each
                    dimension separately; treat the aggregate as a summary of
                    them, not a measurement of its own. Needs `transcript`.
          recall    Ask whether now is the moment to pull saved decisions back
                    in. Call this before starting a subtask, or when you catch
                    yourself re-deriving something — the cases where an agent
                    typically does not think to check its own memory, which is
                    why this is prompt-able at all. Returns the trigger and its
                    threshold, the budget and its basis, and the records worth
                    surfacing — or says plainly that nothing crossed a
                    threshold, which is a different answer from "nothing is
                    saved". Needs `task`; takes `context_sample`.

        Verity cannot see your window, so every answer here is about the text
        you hand over and nothing else.
        """
        if op == "optimize":
            return _missing(
                f'context(op="{op}")', transcript=transcript
            ) or handlers.optimize_context(transcript, task, budget)
        if op == "health":
            return _missing(
                f'context(op="{op}")', transcript=transcript
            ) or handlers.context_health(transcript)
        if op == "recall":
            return _missing(f'context(op="{op}")', task=task) or handlers.recall(
                root, task, context_sample
            )
        raise AssertionError(f"unhandled op {op!r}")  # pragma: no cover

    @server.tool()
    def remember(
        kind: Literal["decision", "constraint", "discovery", "failure"],
        statement: str,
        why: str = "",
        hard: bool = True,
    ) -> str:
        """Write something to durable project memory, permanently.

        Call this the moment you learn or decide something a future session
        would otherwise pay to rediscover. Records are append-only and never
        deleted, so a rejected approach stays visible instead of being quietly
        re-proposed later.

        kinds:
          decision    A choice between approaches, especially a rejection.
                      `statement` is the choice, `why` the rationale.
          constraint  A rule the solution must respect — a dependency you must
                      not add, an interface you must not break. Call this for
                      anything that invalidates the work if violated. Set
                      `hard=False` for a preference rather than a rule.
          discovery   Something non-obvious you learned about the project: how
                      a module is wired, where a behaviour actually lives.
                      This is information you paid tool calls for.
          failure     A dead end. `statement` is what you tried, `why` the
                      error. Call this on every one — it is the most valuable
                      thing to remember on a long task and the easiest to
                      forget, and without it the same approach gets attempted
                      again several hours later.
        """
        return _missing(f'remember(kind="{kind}")', statement=statement) or handlers.remember(
            root, kind, statement, why, hard
        )

    @server.tool()
    def session(
        op: Literal["task", "state", "handoff", "snapshot", "restore", "list"],
        title: str = "",
        description: str = "",
        next_action: str = "",
        budget: int = 2000,
        label: str = "",
        number: int = 0,
    ) -> str:
        """Read back or checkpoint the state of the work in progress.

        Call this at the start of a task, after a context reset, before
        anything risky, and whenever you are unsure whether something was
        already decided or already tried.

        ops:
          task      Record what you are currently working on. Call this first:
                    everything saved afterwards hangs off it, and it is the
                    opening section of any handoff. Needs `title`; takes
                    `description`, `next_action`.
          state     Retrieve everything recorded about the current task,
                    unabridged. Call this when starting fresh on existing work.
          handoff   The same document, fitted to a token budget for a fresh
                    session: task, state, decisions, constraints, discoveries,
                    failures, files, next action. Call this when the context is
                    degrading or you are handing off. Sections drop in a fixed
                    order if the budget is tight and the answer says which
                    went. Takes `budget`.
          snapshot  Capture the current task state as a restorable checkpoint.
                    Call this before anything risky. Takes `label`.
          restore   Return to a checkpoint after a wrong path. Needs `number`.
          list      List the snapshots. Call this before `restore` to find the
                    right number.

        Snapshots cover context only — code rollback is git's job, and Verity
        never modifies your working tree.
        """
        if op == "task":
            return _missing(f'session(op="{op}")', title=title) or handlers.set_task(
                root, title, description, next_action
            )
        if op == "state":
            return handlers.state(root)
        if op == "handoff":
            return handlers.handoff(root, budget)
        if op == "snapshot":
            return handlers.snapshot(root, label)
        if op == "restore":
            return _missing(f'session(op="{op}")', number=number) or handlers.restore(root, number)
        if op == "list":
            return handlers.list_snapshots(root)
        raise AssertionError(f"unhandled op {op!r}")  # pragma: no cover

    @server.tool()
    def code(
        op: Literal["find", "define", "impact", "index"],
        name: str = "",
        task: str = "",
        limit: int = 15,
        force: bool = False,
    ) -> str:
        """Ask the code graph what is actually in this repository.

        Call `op="index"` once at the start of a session — every other op reads
        the graph it builds and will tell you to run it rather than answer from
        an empty index. Then call this before reading files, and before
        asserting that any API exists.

        ops:
          index   Index the repository into a queryable graph. Incremental —
                  unchanged files are skipped — so re-running after edits is
                  cheap. The answer states how much of the tree is actually
                  represented; Python only in this version, and other languages
                  are reported as not read rather than silently missing. Takes
                  `force` to rebuild from scratch.
          find    Find code related to a task, by relationship as well as by
                  name. Call this before opening files. It seeds on text and
                  then follows call, containment, inheritance and test edges,
                  so it surfaces the function that has nothing to do with your
                  search terms but is called by one that does — which grep and
                  embedding search both miss. Every result says why it was
                  included. Needs `task`; takes `limit`.
          define  Check whether a function, class or method actually exists,
                  and where. Call this before asserting an API is available,
                  and whenever you are about to act on a memory of the codebase
                  rather than something you just read. Far cheaper than opening
                  files, and the difference between believing and knowing.
                  Needs `name`.
          impact  See what depends on a symbol before you change it: what calls
                  it and which tests exercise it. Call this before editing any
                  shared function or class. The blast radius is derived from
                  edges, not from a text search for the name. Needs `name`.
        """
        if op == "index":
            return handlers.index(root, force)
        if op == "find":
            return _missing(f'code(op="{op}")', task=task) or handlers.find(root, task, limit)
        if op == "define":
            return _missing(f'code(op="{op}")', name=name) or handlers.define(root, name)
        if op == "impact":
            return _missing(f'code(op="{op}")', name=name) or handlers.impact(root, name)
        raise AssertionError(f"unhandled op {op!r}")  # pragma: no cover

    @server.tool()
    def verify(
        op: Literal["claims", "security", "architecture", "risk"],
        text: str = "",
        paths: list[str] | None = None,
    ) -> str:
        """Check work against the repository before you call it done.

        Call this on your own draft answer, and on the files you touched, near
        the end of a task. Nothing here is a model call and nothing here is a
        proof: these are deterministic checks that say what they cannot see.

        ops:
          claims        Check your own claims against the code graph and saved
                        memory. Call this on a draft response that asserts
                        something checkable — that a symbol exists, that one
                        function calls another, that a file is at some path.
                        Write them in backticks the way you normally format
                        code references (`ClassName.method`, `A` calls `B`).
                        Also flags text resembling a decision already rejected
                        or superseded. A claim it cannot extract is simply not
                        checked, never guessed at. Needs `text`.
          security      Scan for SQL injection and check-then-act races. Call
                        this before finishing a task touching database queries
                        or shared mutable state (caches, counters, session
                        stores). A finding means "worth a human look"; a clean
                        scan means "not this exact shape". See the returned
                        caveats for what each rule cannot see.
          architecture  Check every import against the project's declared
                        dependency policy. Call this after adding a
                        cross-package import. A graph algorithm, not a cycle
                        check: it answers whether this specific import goes
                        somewhere the architecture says it should not.
          risk          Tier files you are about to change: how much
                        verification each earns. Call this before editing
                        several files at once. Returns low/medium/high per file
                        with reasons — blast radius, fan-in, untested public
                        symbols, conventions like `auth/` or `migrations/`. A
                        tier is a *depth*, not a finding: "high" does not mean
                        broken. It gates nothing — run `security` as well,
                        always. Needs `paths`, repo-relative as `index` stored
                        them (`src/pkg/mod.py`); a path that cannot be resolved
                        is reported as such rather than silently tiered low.
        """
        if op == "claims":
            return _missing(f'verify(op="{op}")', text=text) or handlers.check_claims(root, text)
        if op == "security":
            return handlers.check_security(root)
        if op == "architecture":
            return handlers.check_architecture(root)
        if op == "risk":
            if not paths:
                return "No paths given. Pass the files you are about to change."
            return handlers.risk(root, paths)
        raise AssertionError(f"unhandled op {op!r}")  # pragma: no cover

    return server


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="verity-mcp", description="Run the Verity MCP server over stdio."
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("VERITY_ROOT"),
        help=(
            "Project directory holding .verity/ (default: $VERITY_ROOT, then the "
            "working directory). Set this when the client does not launch the "
            "server inside the project -- otherwise state is written where the "
            "client happened to start, silently."
        ),
    )
    args = parser.parse_args()
    build_server(root=args.root).run()


if __name__ == "__main__":  # pragma: no cover
    main()
