"""SQLite-backed storage for the code graph.

Why SQLite and not Neo4j: adoption. A harness that requires a graph database
running before it does anything useful will not be installed. `sqlite3` is in
the standard library, the graph lives in one file inside `.verity/`, and there
is nothing to start. Neo4j remains a plausible optional backend if graph size
ever demands it — the query surface in `query.py` is deliberately narrow
enough to reimplement.

Why not networkx: it would be the first new runtime dependency since the
pivot, to provide traversals that are twenty lines each. The graph shapes this
project needs (neighbours, bounded BFS, cycle detection over imports) are
small, and writing them means they can be tuned to the domain — the cycle
detector reports the actual cycle path, which is what a developer needs, not
merely that one exists.

Incrementality is keyed on content hash, not mtime. A checkout, a rebase, or a
`touch` all change mtime without changing content, and re-parsing a thousand
unchanged files because git updated a timestamp is the difference between a
tool you run constantly and one you avoid.
"""

import json
import sqlite3
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from verityai.core.models import EdgeKind, GraphEdge, GraphNode, NodeKind

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id        TEXT PRIMARY KEY,
    kind      TEXT NOT NULL,
    name      TEXT NOT NULL,
    qualname  TEXT NOT NULL DEFAULT '',
    path      TEXT NOT NULL DEFAULT '',
    line      INTEGER,
    end_line  INTEGER,
    signature TEXT NOT NULL DEFAULT '',
    docstring TEXT NOT NULL DEFAULT '',
    metadata  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS edges (
    source   TEXT NOT NULL,
    target   TEXT NOT NULL,
    kind     TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 1,
    line     INTEGER,
    metadata TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (source, target, kind, line)
);

-- One row per ingested file, so a re-ingest can skip unchanged files and
-- delete the nodes of files that disappeared.
CREATE TABLE IF NOT EXISTS files (
    path         TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    ingested_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(path);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
"""


def _row_to_node(row: sqlite3.Row) -> GraphNode:
    return GraphNode(
        id=row["id"],
        kind=NodeKind(row["kind"]),
        name=row["name"],
        qualname=row["qualname"],
        path=row["path"],
        line=row["line"],
        end_line=row["end_line"],
        signature=row["signature"],
        docstring=row["docstring"],
        metadata=json.loads(row["metadata"]),
    )


def _row_to_edge(row: sqlite3.Row) -> GraphEdge:
    return GraphEdge(
        source=row["source"],
        target=row["target"],
        kind=EdgeKind(row["kind"]),
        resolved=bool(row["resolved"]),
        line=row["line"],
        metadata=json.loads(row["metadata"]),
    )


class GraphStore:
    """The code graph, stored in one SQLite file."""

    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @classmethod
    def for_verity_dir(cls, verity_root: Path) -> "GraphStore":
        """Open the graph belonging to a `.verity/` directory."""
        return cls(Path(verity_root) / "graph.db")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- writing ---------------------------------------------------------

    def add_nodes(self, nodes: Iterable[GraphNode]) -> int:
        """Insert or replace nodes. Returns how many were written.

        REPLACE rather than IGNORE: a node's signature or line numbers change
        as the code does, and an ingest that kept the first version it ever
        saw would drift away from the repository silently.
        """
        rows = [
            (
                node.id,
                node.kind.value,
                node.name,
                node.qualname,
                node.path,
                node.line,
                node.end_line,
                node.signature,
                node.docstring,
                json.dumps(node.metadata),
            )
            for node in nodes
        ]
        self.conn.executemany("INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def add_edges(self, edges: Iterable[GraphEdge]) -> int:
        """Insert or replace edges. Returns how many were written."""
        rows = [
            (
                edge.source,
                edge.target,
                edge.kind.value,
                int(edge.resolved),
                edge.line if edge.line is not None else -1,
                json.dumps(edge.metadata),
            )
            for edge in edges
        ]
        self.conn.executemany("INSERT OR REPLACE INTO edges VALUES (?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def forget_file(self, path: str) -> None:
        """Remove a file's nodes and their edges.

        Called before re-ingesting a changed file and when a file disappears.
        Without it, deleting a function would leave its node in the graph
        forever, and the graph would start asserting the existence of things
        that are gone — the exact failure the Consistency Engine exists to
        catch, committed by the harness itself.
        """
        ids = [
            row["id"] for row in self.conn.execute("SELECT id FROM nodes WHERE path = ?", (path,))
        ]
        if ids:
            placeholders = ",".join("?" * len(ids))
            self.conn.execute(f"DELETE FROM edges WHERE source IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM edges WHERE target IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", ids)
        self.conn.execute("DELETE FROM files WHERE path = ?", (path,))
        self.conn.commit()

    def record_file(self, path: str, content_hash: str, ingested_at: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO files VALUES (?,?,?)", (path, content_hash, ingested_at)
        )
        self.conn.commit()

    def file_hash(self, path: str) -> str | None:
        row = self.conn.execute("SELECT content_hash FROM files WHERE path = ?", (path,)).fetchone()
        return row["content_hash"] if row else None

    def known_files(self) -> set[str]:
        return {row["path"] for row in self.conn.execute("SELECT path FROM files")}

    def clear(self) -> None:
        for table in ("edges", "nodes", "files"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    # --- reading ---------------------------------------------------------

    def get_node(self, node_id: str) -> GraphNode | None:
        row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return _row_to_node(row) if row else None

    def nodes_named(self, name: str, kind: NodeKind | None = None) -> list[GraphNode]:
        """Every node with exactly this name. Ambiguity is the caller's to resolve.

        Returning all matches rather than guessing: two classes named `Store`
        in different modules is normal, and a graph that silently picked one
        would answer "where is Store defined" with a coin flip.
        """
        if kind is None:
            rows = self.conn.execute("SELECT * FROM nodes WHERE name = ?", (name,))
        else:
            rows = self.conn.execute(
                "SELECT * FROM nodes WHERE name = ? AND kind = ?", (name, kind.value)
            )
        return [_row_to_node(row) for row in rows]

    def search_nodes(self, fragment: str, limit: int = 50) -> list[GraphNode]:
        """Nodes whose name or qualname contains `fragment`, case-insensitively."""
        pattern = f"%{fragment}%"
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE name LIKE ? OR qualname LIKE ? "
            "ORDER BY length(name), name LIMIT ?",
            (pattern, pattern, limit),
        )
        return [_row_to_node(row) for row in rows]

    def nodes_in_file(self, path: str) -> list[GraphNode]:
        rows = self.conn.execute("SELECT * FROM nodes WHERE path = ? ORDER BY line", (path,))
        return [_row_to_node(row) for row in rows]

    def all_nodes(self, kind: NodeKind | None = None) -> list[GraphNode]:
        if kind is None:
            rows = self.conn.execute("SELECT * FROM nodes")
        else:
            rows = self.conn.execute("SELECT * FROM nodes WHERE kind = ?", (kind.value,))
        return [_row_to_node(row) for row in rows]

    def edges_from(self, node_id: str, kind: EdgeKind | None = None) -> list[GraphEdge]:
        if kind is None:
            rows = self.conn.execute("SELECT * FROM edges WHERE source = ?", (node_id,))
        else:
            rows = self.conn.execute(
                "SELECT * FROM edges WHERE source = ? AND kind = ?", (node_id, kind.value)
            )
        return [_row_to_edge(row) for row in rows]

    def edges_to(self, node_id: str, kind: EdgeKind | None = None) -> list[GraphEdge]:
        if kind is None:
            rows = self.conn.execute("SELECT * FROM edges WHERE target = ?", (node_id,))
        else:
            rows = self.conn.execute(
                "SELECT * FROM edges WHERE target = ? AND kind = ?", (node_id, kind.value)
            )
        return [_row_to_edge(row) for row in rows]

    def unresolved_edges(self) -> list[GraphEdge]:
        """Edges pointing at names no definition was found for.

        These are the graph's honest admissions of ignorance, and Phase 3's
        raw material: a call to something that exists nowhere is what a
        hallucinated API looks like structurally.
        """
        rows = self.conn.execute("SELECT * FROM edges WHERE resolved = 0")
        return [_row_to_edge(row) for row in rows]

    # --- traversal -------------------------------------------------------

    def neighbours(
        self,
        node_id: str,
        kinds: Iterable[EdgeKind] | None = None,
        direction: str = "out",
    ) -> list[GraphNode]:
        """Nodes one hop away.

        `direction` is "out", "in", or "both". Unresolved edges are skipped:
        their target is a bare name, not a node id, so there is nothing to
        return. `unresolved_edges()` is how you see those.
        """
        wanted = {k.value for k in kinds} if kinds else None
        found: list[GraphNode] = []
        seen: set[str] = set()

        pairs = []
        if direction in ("out", "both"):
            pairs += [(e.target, e) for e in self.edges_from(node_id) if e.resolved]
        if direction in ("in", "both"):
            pairs += [(e.source, e) for e in self.edges_to(node_id) if e.resolved]

        for other_id, edge in pairs:
            if wanted and edge.kind.value not in wanted:
                continue
            if other_id in seen:
                continue
            seen.add(other_id)
            node = self.get_node(other_id)
            if node is not None:
                found.append(node)
        return found

    def reachable(
        self,
        start: str,
        kinds: Iterable[EdgeKind] | None = None,
        direction: str = "out",
        max_depth: int = 2,
    ) -> dict[str, int]:
        """Breadth-first walk from `start`, returning node id -> depth.

        Bounded by `max_depth` because this feeds context assembly, and an
        unbounded walk of a call graph reaches most of a codebase in three or
        four hops. The depth is returned rather than discarded so the caller
        can weight closer nodes more heavily.
        """
        depths = {start: 0}
        queue: deque[str] = deque([start])

        while queue:
            current = queue.popleft()
            depth = depths[current]
            if depth >= max_depth:
                continue
            for node in self.neighbours(current, kinds=kinds, direction=direction):
                if node.id not in depths:
                    depths[node.id] = depth + 1
                    queue.append(node.id)

        del depths[start]
        return depths

    def cycles(self, kind: EdgeKind = EdgeKind.IMPORTS) -> list[list[str]]:
        """Every simple cycle over one edge kind, as a list of node-id paths.

        Iterative DFS with an explicit stack rather than recursion — a deep
        import chain would otherwise risk a RecursionError on exactly the
        pathological codebase where this is most worth running.

        Returns the cycle *path*, not just a boolean, because "there is a
        circular import" is not actionable and "a imports b imports c imports
        a" is.
        """
        adjacency: dict[str, list[str]] = {}
        for row in self.conn.execute(
            "SELECT source, target FROM edges WHERE kind = ? AND resolved = 1", (kind.value,)
        ):
            adjacency.setdefault(row["source"], []).append(row["target"])

        found: list[list[str]] = []
        seen_signatures: set[tuple[str, ...]] = set()
        finished: set[str] = set()

        for root in list(adjacency):
            if root in finished:
                continue

            # (node, index into its neighbour list)
            stack: list[tuple[str, int]] = [(root, 0)]
            on_path: list[str] = [root]
            in_path: set[str] = {root}

            while stack:
                node, index = stack[-1]
                neighbours = adjacency.get(node, [])

                if index >= len(neighbours):
                    stack.pop()
                    finished.add(node)
                    in_path.discard(node)
                    if on_path:
                        on_path.pop()
                    continue

                stack[-1] = (node, index + 1)
                nxt = neighbours[index]

                if nxt in in_path:
                    cycle = on_path[on_path.index(nxt) :] + [nxt]
                    # Rotate to a canonical starting point so the same cycle
                    # discovered from different roots is reported once.
                    body = cycle[:-1]
                    pivot = body.index(min(body))
                    signature = tuple(body[pivot:] + body[:pivot])
                    if signature not in seen_signatures:
                        seen_signatures.add(signature)
                        found.append(list(signature) + [signature[0]])
                elif nxt not in finished:
                    stack.append((nxt, 0))
                    on_path.append(nxt)
                    in_path.add(nxt)

        return found

    # --- reporting -------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Node and edge counts by kind."""
        result: dict[str, int] = {}
        for row in self.conn.execute("SELECT kind, COUNT(*) AS n FROM nodes GROUP BY kind"):
            result[f"nodes.{row['kind']}"] = row["n"]
        for row in self.conn.execute("SELECT kind, COUNT(*) AS n FROM edges GROUP BY kind"):
            result[f"edges.{row['kind']}"] = row["n"]
        result["nodes.total"] = self.conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"]
        result["edges.total"] = self.conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()["n"]
        result["edges.unresolved"] = self.conn.execute(
            "SELECT COUNT(*) AS n FROM edges WHERE resolved = 0"
        ).fetchone()["n"]
        result["files"] = self.conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"]
        return result
