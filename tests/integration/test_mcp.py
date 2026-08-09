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


async def call(server, name, **kwargs):
    """Invoke a tool the way an MCP client would."""
    result = await server.call_tool(name, kwargs)
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
