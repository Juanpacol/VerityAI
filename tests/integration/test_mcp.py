"""Tests for the MCP server.

Skipped when the SDK is absent rather than failing, since it is an optional
extra. What is asserted: the tools exist under stable names, and each one
behaves identically to the CLI path it wraps -- the guarantee that lets a
person reproduce by hand whatever an agent saw.
"""

import json

import pytest

pytest.importorskip("mcp", reason="MCP SDK is an optional extra")
pytestmark = pytest.mark.integration

from verityai.mcp.server import build_server  # noqa: E402

TRANSCRIPT = json.dumps(
    [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Add rate limiting."},
        {"role": "assistant", "content": "DECISION: token bucket per API key."},
        {"role": "tool", "content": "ok"},
        {"role": "tool", "content": "build log\n" * 400},
        {"role": "assistant", "content": "The middleware is in src/api/rate_limit.py."},
        {"role": "assistant", "content": "The middleware is in src/api/rate_limit.py."},
    ]
)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return build_server()


async def call(server, tool_name, /, **kwargs):
    """Invoke a tool the way an MCP client would.

    The helper's own parameters are positional-only: several tools take an
    argument literally called `name`, which would otherwise collide with this
    signature rather than being forwarded.
    """
    result = await server.call_tool(tool_name, kwargs)
    # FastMCP returns (content_blocks, raw) across versions; normalize to text.
    blocks = result[0] if isinstance(result, tuple) else result
    return "\n".join(getattr(block, "text", str(block)) for block in blocks)


class TestToolSurface:
    @pytest.mark.asyncio
    async def test_every_expected_tool_is_registered(self, server):
        names = {tool.name for tool in await server.list_tools()}

        assert names == {
            "optimize_context",
            "context_health",
            "set_task",
            "save_decision",
            "save_constraint",
            "save_discovery",
            "save_failure",
            "get_state",
            "handoff",
            "snapshot",
            "restore",
            "list_snapshots",
            "build_code_graph",
            "find_relevant_code",
            "check_symbol_exists",
            "impact_of_changing",
            "check_claims",
            "check_security",
            "check_architecture",
        }

    @pytest.mark.asyncio
    async def test_tools_describe_when_to_call_them(self, server):
        """A tool an agent never thinks to call is worth nothing."""
        for tool in await server.list_tools():
            assert tool.description
            assert "Call this" in tool.description, tool.name


class TestContextTools:
    @pytest.mark.asyncio
    async def test_optimize_context_reduces_and_explains(self, server):
        output = await call(server, "optimize_context", transcript=TRANSCRIPT, budget=200)

        assert "Pruned" in output
        assert "saved" in output
        assert "--- CONTEXT ---" in output

    @pytest.mark.asyncio
    async def test_optimize_context_reports_its_counting_method(self, server):
        output = await call(server, "optimize_context", transcript=TRANSCRIPT)

        assert "counted with" in output

    @pytest.mark.asyncio
    async def test_critical_content_survives_a_tight_budget(self, server):
        output = await call(server, "optimize_context", transcript=TRANSCRIPT, budget=50)

        assert "token bucket" in output

    @pytest.mark.asyncio
    async def test_context_health_reports_dimensions_not_just_a_score(self, server):
        output = await call(server, "context_health", transcript=TRANSCRIPT)

        assert "Window usage" in output
        assert "Redundancy" in output
        assert "BY RELEVANCE" in output


class TestMemoryTools:
    @pytest.mark.asyncio
    async def test_state_round_trips_through_get_state(self, server):
        await call(server, "set_task", title="add rate limiting", next_action="write the test")
        await call(server, "save_decision", statement="token bucket", why="bursts")
        await call(server, "save_constraint", statement="no new dependencies")
        await call(server, "save_discovery", statement="middleware runs after auth")
        await call(server, "save_failure", attempted="fixed window", error="rejected bursts")

        state = await call(server, "get_state")

        for fragment in (
            "add rate limiting",
            "token bucket",
            "bursts",
            "no new dependencies",
            "middleware runs after auth",
            "fixed window",
            "write the test",
        ):
            assert fragment in state, fragment

    @pytest.mark.asyncio
    async def test_the_store_is_created_without_an_explicit_init(self, server, tmp_path):
        """An agent cannot usefully react to 'run verity init first'."""
        await call(server, "save_decision", statement="something decided")

        assert (tmp_path / ".verity" / "state" / "decisions.jsonl").exists()

    @pytest.mark.asyncio
    async def test_handoff_reports_its_token_cost(self, server):
        await call(server, "set_task", title="the task")

        output = await call(server, "handoff")

        assert "# HANDOFF" in output
        assert "tokens" in output

    @pytest.mark.asyncio
    async def test_handoff_names_dropped_sections(self, server):
        await call(server, "set_task", title="the task")
        await call(server, "save_discovery", statement="a fairly long discovery " * 20)

        output = await call(server, "handoff", budget=30)

        assert "dropped to fit budget" in output


class TestGraphTools:
    @pytest.fixture
    def with_code(self, tmp_path, server):
        """A tiny project in the server's working directory."""
        (tmp_path / "app.py").write_text(
            '"""App."""\n\n\n'
            "def apply_ceiling(n, cap):\n    return min(n, cap)\n\n\n"
            "def rate_limit_request(key, n):\n"
            '    """Rate limiting."""\n'
            "    return apply_ceiling(n, 100)\n"
        )
        return server

    @pytest.mark.asyncio
    async def test_graph_tools_say_when_the_graph_is_empty(self, server):
        """Better than returning nothing and letting the agent infer absence."""
        output = await call(server, "check_symbol_exists", name="anything")

        assert "empty" in output.lower()
        assert "build_code_graph" in output

    @pytest.mark.asyncio
    async def test_building_reports_coverage(self, with_code):
        output = await call(with_code, "build_code_graph")

        assert "Python files in the graph" in output
        assert "nodes" in output and "edges" in output

    @pytest.mark.asyncio
    async def test_a_real_symbol_is_found_with_its_location(self, with_code):
        await call(with_code, "build_code_graph")

        output = await call(with_code, "check_symbol_exists", name="rate_limit_request")

        assert "FOUND" in output
        assert "app.py" in output

    @pytest.mark.asyncio
    async def test_an_invented_symbol_is_reported_as_absent(self, with_code):
        """The tool an agent should call before asserting an API exists."""
        await call(with_code, "build_code_graph")

        output = await call(with_code, "check_symbol_exists", name="refresh_token_nonexistent")

        assert "NOT FOUND" in output
        assert "Do not assume it exists" in output

    @pytest.mark.asyncio
    async def test_relevant_code_expands_beyond_lexical_matches(self, with_code):
        await call(with_code, "build_code_graph")

        output = await call(with_code, "find_relevant_code", task="rate limiting")

        assert "rate_limit_request" in output
        assert "apply_ceiling" in output, "should be reached via the call edge"

    @pytest.mark.asyncio
    async def test_impact_lists_callers(self, with_code):
        await call(with_code, "build_code_graph")

        output = await call(with_code, "impact_of_changing", name="apply_ceiling")

        assert "Called by" in output
        assert "rate_limit_request" in output

    @pytest.mark.asyncio
    async def test_impact_flags_that_missing_tests_over_report(self, with_code):
        await call(with_code, "build_code_graph")

        output = await call(with_code, "impact_of_changing", name="apply_ceiling")

        assert "over-reports" in output


class TestConsistencyTools:
    @pytest.fixture
    def with_code(self, tmp_path, server):
        (tmp_path / "app.py").write_text(
            "def apply_ceiling(n, cap):\n    return min(n, cap)\n\n\n"
            "def rate_limit_request(key, n):\n    return apply_ceiling(n, 100)\n"
        )
        return server

    @pytest.mark.asyncio
    async def test_a_hallucinated_symbol_is_flagged(self, with_code):
        await call(with_code, "build_code_graph")

        output = await call(
            with_code, "check_claims", text="I used `TotallyInventedSymbolXYZ` for this."
        )

        assert "CONTRADICTION" in output
        assert "no definition" in output

    @pytest.mark.asyncio
    async def test_a_real_symbol_is_not_flagged(self, with_code):
        await call(with_code, "build_code_graph")

        output = await call(with_code, "check_claims", text="`apply_ceiling` clamps the value.")

        assert "All claims check out" in output

    @pytest.mark.asyncio
    async def test_a_real_relation_is_verified(self, with_code):
        await call(with_code, "build_code_graph")

        output = await call(
            with_code,
            "check_claims",
            text="`rate_limit_request` calls `apply_ceiling` to clamp the count.",
        )

        assert "All claims check out" in output

    @pytest.mark.asyncio
    async def test_no_checkable_claims_is_reported_plainly(self, with_code):
        output = await call(with_code, "check_claims", text="Just a plain sentence.")

        assert "No checkable claims" in output

    @pytest.mark.asyncio
    async def test_decision_checks_work_without_a_graph(self, server):
        """Decision checks read .verity/ memory, not the code graph, and
        must work even before build_code_graph has ever been called."""
        await call(server, "save_decision", statement="use a fixed window counter")

        output = await call(
            server, "check_claims", text="I'll use a fixed window counter for rate limiting."
        )

        # An ACTIVE (never-rejected) decision must not be flagged.
        assert "CONTRADICTION" not in output


class TestReliabilityTools:
    @pytest.fixture
    def with_vulnerable_code(self, tmp_path, server):
        (tmp_path / "db.py").write_text(
            "def get_user(conn, name):\n"
            '    query = "SELECT * FROM users WHERE name = " + name\n'
            "    return conn.execute(query)\n"
        )
        return server

    @pytest.mark.asyncio
    async def test_check_security_finds_a_real_vulnerability(self, with_vulnerable_code):
        output = await call(with_vulnerable_code, "check_security")

        assert "SQL Injection" in output
        assert "1 violation" in output

    @pytest.mark.asyncio
    async def test_check_security_is_clean_on_safe_code(self, tmp_path, server):
        (tmp_path / "db.py").write_text(
            "def get_user(conn, name):\n"
            '    return conn.execute("SELECT * FROM users WHERE name = ?", (name,))\n'
        )

        output = await call(server, "check_security")

        assert "No violations found" in output

    @pytest.mark.asyncio
    async def test_check_architecture_is_clean_on_a_small_project(self, tmp_path, server):
        (tmp_path / "app.py").write_text("x = 1\n")

        output = await call(server, "check_architecture")

        assert "No violations found" in output


class TestSnapshotTools:
    @pytest.mark.asyncio
    async def test_snapshot_and_restore_round_trip(self, server):
        await call(server, "set_task", title="original task")
        await call(server, "snapshot", label="checkpoint")
        await call(server, "set_task", title="changed task")

        await call(server, "restore", number=1)

        assert "original task" in await call(server, "get_state")

    @pytest.mark.asyncio
    async def test_restoring_a_missing_snapshot_explains_itself(self, server):
        output = await call(server, "restore", number=99)

        assert "No snapshot" in output
        assert "list_snapshots" in output

    @pytest.mark.asyncio
    async def test_listing_with_no_snapshots_is_not_an_error(self, server):
        assert "No snapshots yet" in await call(server, "list_snapshots")

    @pytest.mark.asyncio
    async def test_snapshots_are_listed_with_labels(self, server):
        await call(server, "snapshot", label="first")
        await call(server, "snapshot", label="second")

        output = await call(server, "list_snapshots")

        assert "001" in output and "first" in output
        assert "002" in output and "second" in output
