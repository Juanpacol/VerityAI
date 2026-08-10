"""Querying the code graph — including the part that makes it worth building.

Most of this module is thin: callers, callees, tests, definitions. The one
that justifies the graph's existence is `context_for`, which answers "what
should the agent be looking at for this task" by *relationship* rather than by
text similarity alone.

The difference matters concretely. Ask a vector store for "rate limiting" and
you get the chunks that mention rate limiting. Ask this, and you also get the
function that calls the rate limiter without naming it, the test that exercises
it, and the class it inherits from — because those are edges, and edges are
things text similarity structurally cannot see. That is the neuro-symbolic
claim narrowed to something checkable: the deterministic structure supplies
what the embedding cannot.

Ranking is lexical (BM25, reused from `context/rank.py`) for the seeds, then
graph expansion for the neighbourhood, with a decay per hop. No model is
called. Every returned item carries *why* it was included — matched the query,
called by a match, tests a match — so a developer can argue with the result
rather than accept it.
"""

from dataclasses import dataclass, field

from verityai.context.rank import bm25_rank
from verityai.core.models import EdgeKind, GraphEdge, GraphNode, NodeKind
from verityai.graph.store import GraphStore

# Kinds worth surfacing as "code the agent should look at". FILE and EXTERNAL
# are excluded as seeds: a whole file is too coarse to be an answer, and an
# external module has no source here to read.
_SEED_KINDS = (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS, NodeKind.TEST)

# Score multiplier per hop away from a seed. 0.4 is steep on purpose --
# two hops through a call graph already reaches most of a codebase, so distant
# nodes must fall off fast enough that they cannot outrank a direct match.
_HOP_DECAY = 0.4


@dataclass
class Relevant:
    """A node the graph thinks is relevant, and the reason it thinks so."""

    node: GraphNode
    score: float
    depth: int
    reasons: list[str] = field(default_factory=list)

    @property
    def location(self) -> str:
        return f"{self.node.path}:{self.node.line}" if self.node.line else self.node.path


class GraphQuery:
    """Read-only questions about the code graph."""

    def __init__(self, store: GraphStore):
        self.store = store

    # --- definitions -----------------------------------------------------

    def define(self, name: str) -> list[GraphNode]:
        """Where is `name` defined? Accepts a bare name or a qualname.

        Returns every match. Two classes named `Store` in different modules is
        normal, and picking one would be a coin flip dressed up as an answer.
        """
        exact = self.store.nodes_named(name)
        if exact:
            return exact
        return [
            node
            for node in self.store.search_nodes(name)
            if node.qualname == name or node.name == name
        ] or self.store.search_nodes(name, limit=10)

    def exists(self, name: str) -> bool:
        """Is there any definition of this name in the repository?

        The seed of Phase 3's hallucination check: an agent asserting that
        `AuthService.refresh_token` exists can be checked against this before
        anyone spends a tool call opening files.
        """
        return bool(self.store.nodes_named(name)) or bool(
            [n for n in self.store.search_nodes(name, limit=5) if n.qualname.endswith(name)]
        )

    # --- relationships ---------------------------------------------------

    def callers(self, node_id: str) -> list[GraphNode]:
        """What calls this. The blast radius of changing it."""
        return self.store.neighbours(node_id, kinds=[EdgeKind.CALLS], direction="in")

    def callees(self, node_id: str) -> list[GraphNode]:
        """What this calls. What you need to understand to read it."""
        return self.store.neighbours(node_id, kinds=[EdgeKind.CALLS], direction="out")

    def tests_for(self, node_id: str) -> list[GraphNode]:
        """Tests that exercise this node.

        Derived from resolved call edges, not from name matching -- a test
        called `test_budget_never_drops_critical` exercises
        `ContextPipeline.run`, and no string comparison would find that.
        """
        return self.store.neighbours(node_id, kinds=[EdgeKind.TESTS], direction="in")

    def untested(self) -> list[GraphNode]:
        """Public symbols no test *edge* reaches. Over-reports, by construction.

        Deterministic — a set difference over edges, no model involved. But it
        is an over-approximation and must always be presented as one, because
        it can only see calls the ingester managed to resolve.

        The systematic blind spot: code exercised indirectly. A CLI command
        invoked as `runner.invoke(app, ["build"])` is genuinely tested, and no
        resolvable call edge points at it, so it lands in this list. Same for
        anything reached through a framework, a dispatch table, or a method on
        a variable whose type could not be inferred.

        So this answers "what has no *direct* test edge", which is a useful
        place to start looking and is not a coverage measurement. Use
        `coverage.py` for that. `untested_caveat()` carries this warning to
        every caller so the number is never displayed bare.
        """
        result = []
        for node in self.store.all_nodes():
            if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
                continue
            if node.name.startswith("_"):
                continue
            if not self.tests_for(node.id):
                result.append(node)
        return result

    def untested_caveat(self) -> str:
        """The warning that must accompany any `untested()` result."""
        unresolved = self.store.stats().get("edges.unresolved", 0)
        return (
            f"Over-approximation: {unresolved:,} calls could not be resolved to a "
            "definition, so code exercised indirectly (CLI commands driven through a "
            "test runner, framework dispatch, methods on untyped locals) appears here "
            "despite being tested. This is a place to start looking, not a coverage "
            "measurement -- use coverage.py for that."
        )

    def import_cycles(self) -> list[list[GraphNode]]:
        """Circular imports, as full paths rather than a yes/no.

        A graph algorithm, never a model call -- this is the canonical case
        from the design document's "deterministic first" rule.
        """
        cycles = []
        for path in self.store.cycles(EdgeKind.IMPORTS):
            nodes = [self.store.get_node(node_id) for node_id in path]
            cycles.append([node for node in nodes if node is not None])
        return cycles

    def unresolved_calls(self, limit: int = 50) -> list[GraphEdge]:
        """Calls the ingester could not tie to a definition.

        Mostly builtins and methods on locals, which is expected and fine.
        Phase 3 will filter these against a stdlib list; what matters now is
        that they were kept rather than discarded.
        """
        return self.store.unresolved_edges()[:limit]

    def file_dependencies(self, path: str) -> dict[str, list[str]]:
        """What a file imports and which files import it."""
        file_id = GraphNode.make_id(NodeKind.FILE, path)
        return {
            "imports": sorted(
                node.qualname or node.name
                for node in self.store.neighbours(
                    file_id, kinds=[EdgeKind.IMPORTS], direction="out"
                )
            ),
            "imported_by": sorted(
                node.path or node.name
                for node in self.store.neighbours(file_id, kinds=[EdgeKind.IMPORTS], direction="in")
            ),
        }

    # --- the one that matters --------------------------------------------

    def context_for(
        self,
        task: str,
        limit: int = 20,
        max_depth: int = 2,
    ) -> list[Relevant]:
        """Code relevant to `task`, found lexically then expanded by relationship.

        Two passes:

        1. **Seed** — BM25 over each node's name, qualname, signature and
           docstring. This is the part a vector store also does.
        2. **Expand** — walk out from the seeds along calls, containment,
           inheritance and tests, decaying the score per hop.

        The second pass is the whole point. A function that never mentions the
        task's vocabulary but is called by one that does is relevant, and only
        the graph knows it.

        Every result carries its reasons, so a wrong answer can be diagnosed
        rather than merely disbelieved.
        """
        candidates = [node for node in self.store.all_nodes() if node.kind in _SEED_KINDS]
        if not candidates or not task.strip():
            return []

        documents = [
            f"{node.name} {node.qualname} {node.signature} {node.docstring}" for node in candidates
        ]
        ranks, scores = bm25_rank(task, documents)
        if not ranks:
            return []

        by_id = {node.id: node for node in candidates}
        found: dict[str, Relevant] = {}

        # Pass 1: direct lexical matches.
        top_seeds = sorted(ranks, key=lambda idx: ranks[idx])[: max(limit // 2, 5)]
        max_score = max(scores.values()) or 1.0

        for idx in top_seeds:
            node = candidates[idx]
            found[node.id] = Relevant(
                node=node,
                score=scores[idx] / max_score,
                depth=0,
                reasons=[f"matches {task!r} (rank {ranks[idx]})"],
            )

        # Pass 2: expand along edges.
        for seed_id in list(found):
            reached = self.store.reachable(
                seed_id,
                kinds=[EdgeKind.CALLS, EdgeKind.CONTAINS, EdgeKind.INHERITS, EdgeKind.TESTS],
                direction="both",
                max_depth=max_depth,
            )
            seed_name = found[seed_id].node.name

            for node_id, depth in reached.items():
                # Distinct from the seed `node` bound in pass 1: that one is a
                # direct lexical match and always exists, this one is a
                # neighbour that may not be in the graph at all.
                neighbour = by_id.get(node_id) or self.store.get_node(node_id)
                if neighbour is None or neighbour.kind not in _SEED_KINDS:
                    continue

                contribution = found[seed_id].score * (_HOP_DECAY**depth)
                reason = self._describe_link(seed_id, node_id, seed_name)

                if node_id in found:
                    existing = found[node_id]
                    # A node reachable from several seeds is more relevant than
                    # one reachable from a single seed, so contributions add
                    # rather than replace -- but a direct match keeps depth 0.
                    existing.score += contribution
                    if reason not in existing.reasons:
                        existing.reasons.append(reason)
                else:
                    found[node_id] = Relevant(
                        node=neighbour, score=contribution, depth=depth, reasons=[reason]
                    )

        ranked = sorted(found.values(), key=lambda r: (-r.score, r.node.path, r.node.line or 0))
        return ranked[:limit]

    def _describe_link(self, seed_id: str, node_id: str, seed_name: str) -> str:
        """Say in words how `node_id` connects to the seed."""
        for edge in self.store.edges_from(node_id):
            if edge.target == seed_id and edge.resolved:
                verb = {
                    EdgeKind.CALLS: "calls",
                    EdgeKind.TESTS: "tests",
                    EdgeKind.CONTAINS: "contains",
                    EdgeKind.INHERITS: "inherits from",
                }.get(edge.kind, "relates to")
                return f"{verb} {seed_name}"

        for edge in self.store.edges_to(node_id):
            if edge.source == seed_id and edge.resolved:
                verb = {
                    EdgeKind.CALLS: "called by",
                    EdgeKind.TESTS: "tested by",
                    EdgeKind.CONTAINS: "contained in",
                    EdgeKind.INHERITS: "base of",
                }.get(edge.kind, "related to")
                return f"{verb} {seed_name}"

        return f"reachable from {seed_name}"


def render_relevant(results: list[Relevant]) -> str:
    """Format `context_for` output for a terminal or an agent."""
    if not results:
        return "No relevant code found. Has the graph been built? Try `verity graph build`."

    lines = []
    for item in results:
        lines.append(f"  {item.node.kind.value:<9} {item.node.qualname or item.node.name}")
        lines.append(f"            {item.location}")
        if item.node.signature:
            lines.append(f"            {item.node.signature}")
        lines.append(f"            why: {'; '.join(item.reasons)}")
        lines.append("")
    return "\n".join(lines)
