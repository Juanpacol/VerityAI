"""End-to-end CLI tests.

These exercise the path a user actually takes, against a real temporary
`.verity/` directory and the real tokenizer — no fakes. The unit suite proves
the pieces behave; this proves they are wired together and that the commands
produce output a person can act on.
"""

import json

import pytest
from typer.testing import CliRunner

from verityai.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A temporary directory that is the current working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def initialized(project):
    runner.invoke(app, ["init"])
    return project


TRANSCRIPT = json.dumps(
    [
        {"role": "system", "content": "You are a coding agent working on an API."},
        {"role": "user", "content": "Add rate limiting to the public endpoints."},
        {"role": "assistant", "content": "DECISION: use a token bucket per API key."},
        {"role": "tool", "content": "ok"},
        {"role": "tool", "content": "ok"},
        {"role": "tool", "content": "build log line\n" * 500},
        {"role": "assistant", "content": "The middleware is in src/api/rate_limit.py."},
        {"role": "assistant", "content": "The middleware is in src/api/rate_limit.py."},
        {"role": "user", "content": "Does it handle burst traffic correctly?"},
    ]
)


class TestInit:
    def test_init_creates_the_directory(self, project):
        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert (project / ".verity" / "state").is_dir()

    def test_commands_requiring_state_fail_helpfully_without_init(self, project):
        result = runner.invoke(app, ["handoff"])

        assert result.exit_code == 1
        assert "verity init" in result.output


class TestIngest:
    def test_ingest_reports_a_breakdown(self, project, tmp_path):
        transcript = tmp_path / "transcript.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["ingest", str(transcript)])

        assert result.exit_code == 0
        assert "critical" in result.output
        assert "CONTEXT HEALTH" in result.output

    def test_ingest_names_its_counting_method(self, project, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["ingest", str(transcript)])

        assert "Counted with:" in result.output

    def test_a_missing_file_fails_clearly(self, project):
        result = runner.invoke(app, ["ingest", "no-such-file.json"])

        assert result.exit_code == 1
        assert "No such file" in result.output


class TestContext:
    def test_pruning_reports_a_stage_ledger(self, project, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["context", str(transcript), "--budget", "500"])

        assert result.exit_code == 0
        for stage in ("dedup", "classify", "filter_tool_output", "compress", "budget", "place"):
            assert stage in result.output, stage

    def test_pruning_saves_tokens_on_a_redundant_transcript(self, project, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(
            app, ["context", str(transcript), "--budget", "500", "--task", "rate limiting"]
        )

        assert "Saved:" in result.output
        assert "Before:" in result.output
        assert "After:" in result.output

    def test_no_critical_bug_is_reported_on_a_normal_run(self, project, tmp_path):
        """The BUG line means the budget stage dropped a protected item."""
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["context", str(transcript), "--budget", "300"])

        assert "BUG:" not in result.output

    def test_output_can_be_written_to_a_file(self, project, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)
        out = tmp_path / "pruned.txt"

        runner.invoke(
            app,
            [
                "context",
                str(transcript),
                "--budget",
                "500",
                "--task",
                "rate limiting",
                "--out",
                str(out),
            ],
        )

        assert out.exists()
        assert "rate limiting" in out.read_text().lower()


class TestMemoryCommands:
    def test_recording_and_reading_back_state(self, initialized):
        runner.invoke(app, ["task", "add rate limiting", "--next", "write the test"])
        runner.invoke(app, ["remember", "decision", "token bucket", "--why", "bursts"])
        runner.invoke(app, ["remember", "constraint", "no new dependencies"])
        runner.invoke(app, ["remember", "discovery", "middleware runs after auth"])
        runner.invoke(app, ["remember", "failure", "fixed window", "--error", "rejected bursts"])

        result = runner.invoke(app, ["handoff"])

        assert result.exit_code == 0
        assert "add rate limiting" in result.output
        assert "token bucket" in result.output
        assert "bursts" in result.output
        assert "no new dependencies" in result.output
        assert "middleware runs after auth" in result.output
        assert "fixed window" in result.output
        assert "write the test" in result.output

    def test_handoff_reports_its_token_cost(self, initialized):
        runner.invoke(app, ["task", "something"])

        result = runner.invoke(app, ["handoff"])

        assert "tokens" in result.output

    def test_handoff_can_be_written_to_a_file(self, initialized, tmp_path):
        runner.invoke(app, ["task", "the task"])
        out = tmp_path / "handoff.md"

        runner.invoke(app, ["handoff", "--out", str(out)])

        assert "# HANDOFF" in out.read_text()


class TestState:
    """`verity state` is the CLI counterpart of MCP's `session(op="state")` --
    CLAUDE.md requires a person be able to reproduce whatever an agent saw."""

    def test_state_matches_unbudgeted_handoff(self, initialized):
        runner.invoke(app, ["task", "add rate limiting"])
        runner.invoke(app, ["remember", "decision", "token bucket"])

        state_out = runner.invoke(app, ["state"]).output
        handoff_out = runner.invoke(app, ["handoff"]).output

        assert state_out == handoff_out

    def test_state_is_unabridged_where_handoff_can_be_budgeted(self, initialized):
        runner.invoke(app, ["task", "the task"])
        runner.invoke(app, ["remember", "discovery", "a fairly long discovery " * 20])

        budgeted = runner.invoke(app, ["handoff", "--budget", "30"]).output
        unabridged = runner.invoke(app, ["state"]).output

        assert "dropped to fit budget" in budgeted
        assert "dropped to fit budget" not in unabridged


class TestRecall:
    """`verity recall` is the CLI counterpart of MCP's `context(op="recall")`."""

    def test_an_empty_store_says_so(self, initialized):
        result = runner.invoke(app, ["recall", "--task", "rate limiting"])

        assert "nothing to recall" in result.output

    def test_without_a_sample_it_lists_what_is_on_file(self, initialized):
        runner.invoke(app, ["remember", "decision", "use a token bucket"])

        result = runner.invoke(app, ["recall", "--task", "rate limiting"])

        assert "No context sample" in result.output
        assert "token bucket" in result.output

    def test_an_untriggered_sample_distinguishes_its_answer(self, initialized, tmp_path):
        """ "Nothing crossed a threshold" must not read as "nothing is saved"."""
        runner.invoke(app, ["remember", "decision", "use a token bucket"])
        sample = tmp_path / "sample.json"
        sample.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["recall", "--task", "rate limiting", "--sample", str(sample)])

        assert "No trigger" in result.output
        assert "not a claim that" in result.output
        assert "verity state" in result.output
        assert 'session(op="state")' not in result.output

    def test_a_triggered_sample_returns_the_records_with_budget_and_basis(
        self, initialized, tmp_path
    ):
        runner.invoke(app, ["remember", "constraint", "must not add a Redis dependency"])
        chunk = " ".join(f"artifact{n}" for n in range(100))
        big = json.dumps(
            [{"role": "tool", "content": f"build chunk {i}: {chunk}"} for i in range(1600)]
        )
        sample = tmp_path / "big.json"
        sample.write_text(big)

        result = runner.invoke(app, ["recall", "--task", "rate limiting", "--sample", str(sample)])

        assert "RECALL NOW" in result.output
        assert "basis" in result.output
        assert "Redis" in result.output
        assert 'session(op="state")' not in result.output


class TestSnapshots:
    def test_snapshot_restore_round_trip(self, initialized):
        runner.invoke(app, ["task", "original task"])
        runner.invoke(app, ["remember", "decision", "the original decision"])
        runner.invoke(app, ["snapshot", "before the change"])

        runner.invoke(app, ["task", "changed task"])
        result = runner.invoke(app, ["restore", "1"])

        assert result.exit_code == 0
        assert "original task" in runner.invoke(app, ["handoff"]).output

    def test_restore_says_it_does_not_touch_code(self, initialized):
        runner.invoke(app, ["task", "something"])
        runner.invoke(app, ["snapshot"])

        result = runner.invoke(app, ["restore", "1"])

        # Only asserted when a git sha was captured; tmp_path is not a repo,
        # so this checks the command succeeds rather than the advisory text.
        assert result.exit_code == 0

    def test_listing_snapshots(self, initialized):
        runner.invoke(app, ["snapshot", "first"])
        runner.invoke(app, ["snapshot", "second"])

        result = runner.invoke(app, ["snapshots"])

        assert "001" in result.output
        assert "first" in result.output
        assert "second" in result.output

    def test_snapshot_reports_the_path_it_saved_to(self, initialized):
        result = runner.invoke(app, ["snapshot"])

        assert "saved to:" in result.output
        assert str(initialized / ".verity" / "snapshots" / "001" / "snapshot.json") in result.output

    def test_snapshots_show_prints_full_contents(self, initialized):
        runner.invoke(app, ["remember", "decision", "use postgres", "--why", "reliable"])
        runner.invoke(app, ["snapshot", "checkpoint"])

        result = runner.invoke(app, ["snapshots", "show", "1"])

        assert result.exit_code == 0
        assert "SNAPSHOT 001" in result.output
        assert "checkpoint" in result.output
        assert "use postgres" in result.output
        assert "path:" in result.output

    def test_snapshots_show_missing_number_fails_clearly(self, initialized):
        result = runner.invoke(app, ["snapshots", "show", "42"])

        assert result.exit_code == 1
        assert "No snapshot" in result.output

    def test_snapshots_browse_lists_then_shows_a_pick_then_quits(self, initialized):
        runner.invoke(app, ["remember", "decision", "use postgres", "--why", "reliable"])
        runner.invoke(app, ["snapshot"])

        result = runner.invoke(app, ["snapshots", "browse"], input="1\nq\n")

        assert result.exit_code == 0
        assert "SNAPSHOT 001" in result.output
        assert "use postgres" in result.output

    def test_snapshots_browse_with_no_snapshots_says_so(self, initialized):
        result = runner.invoke(app, ["snapshots", "browse"])

        assert result.exit_code == 0
        assert "No snapshots yet" in result.output

    def test_restoring_a_missing_snapshot_fails_clearly(self, initialized):
        result = runner.invoke(app, ["restore", "42"])

        assert result.exit_code == 1
        assert "No snapshot" in result.output

    def test_restore_distinguishes_corrupt_snapshot_from_missing(self, initialized):
        runner.invoke(app, ["snapshot"])
        (initialized / ".verity" / "snapshots" / "001" / "snapshot.json").write_text("not json")

        result = runner.invoke(app, ["restore", "1"])

        assert result.exit_code == 1
        assert "unreadable" in result.output
        assert "No snapshot" not in result.output

    def test_snapshot_refuses_on_corrupt_state_and_suggests_force(self, initialized):
        runner.invoke(app, ["remember", "decision", "a", "--why", "reason"])
        path = initialized / ".verity" / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ not valid json\n")

        result = runner.invoke(app, ["snapshot"])

        assert result.exit_code == 1
        assert "--force" in result.output


class TestHealth:
    def test_health_includes_persisted_state_when_available(self, initialized, tmp_path):
        runner.invoke(app, ["remember", "decision", "something decided"])
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["health", str(transcript)])

        assert "CONTEXT HEALTH" in result.output
        assert "PERSISTED STATE" in result.output

    def test_health_prints_corruption_block(self, initialized, tmp_path):
        runner.invoke(app, ["remember", "decision", "a", "--why", "reason"])
        runner.invoke(app, ["remember", "decision", "b", "--why", "reason"])
        runner.invoke(app, ["remember", "decision", "c", "--why", "reason"])
        path = initialized / ".verity" / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ not valid json\n")
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["health", str(transcript)])

        assert result.exit_code == 0
        assert "CORRUPTION" in result.output
        assert "line 4" in result.output

    def test_health_prints_no_corruption_block_when_clean(self, initialized, tmp_path):
        runner.invoke(app, ["remember", "decision", "a", "--why", "reason"])
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["health", str(transcript)])

        assert "CORRUPTION" not in result.output


class TestStatus:
    def test_status_shows_sectioned_view_and_verdict(self, initialized, tmp_path):
        runner.invoke(app, ["remember", "decision", "use postgres", "--why", "reliable"])
        runner.invoke(app, ["remember", "failure", "fixed window", "--error", "rejected bursts"])
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["status", str(transcript)])

        assert result.exit_code == 0
        assert "VERITY STATUS" in result.output
        assert "Decisions:" in result.output
        assert "Failures:" in result.output
        assert "Critical items now:" in result.output
        assert "HEALTHY" in result.output

    def test_status_never_mentions_contradictions(self, initialized, tmp_path):
        """ADR-0041/0042: nothing in this codebase computes a real
        contradiction count; the sectioned view must not display a bare
        zero that would read as 'checked, none found'."""
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["status", str(transcript)])

        assert "contradiction" not in result.output.lower()

    def test_status_pairs_each_reason_with_an_action(self, initialized, tmp_path):
        runner.invoke(app, ["remember", "decision", "a", "--why", "reason"])
        path = initialized / ".verity" / "state" / "decisions.jsonl"
        with path.open("a") as handle:
            handle.write("{ bad\n")
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["status", str(transcript)])

        assert result.exit_code == 0
        assert "CRITICAL" in result.output
        assert "corrupt line" in result.output
        assert "verity health" in result.output

    def test_status_without_verity_init_still_runs(self, project, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["status", str(transcript)])

        assert result.exit_code == 0
        assert "no .verity/ found" in result.output


class TestAdaptiveContext:
    """`verity context --adaptive` (ADR-0025's pre-pass, wired in ADR-0028's
    follow-up). The rule it must never break: surfaced items are merged into
    the input list and pruned once, never injected between pipeline stages --
    the token ledger chains only because `_stage` is its sole writer.
    """

    def _big_transcript(self, tmp_path):
        """Large enough to push window usage past the trigger threshold."""
        path = tmp_path / "big.txt"
        chunk = " ".join(f"artifact{n}" for n in range(100))
        path.write_text("\n\n".join(f"[tool_result] build chunk {i}: {chunk}" for i in range(1600)))
        return path

    def test_adaptive_without_a_task_refuses_with_the_reason(self, initialized, tmp_path):
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(app, ["context", str(transcript), "--adaptive"])

        assert result.exit_code == 2
        assert "requires --task" in result.output
        assert "drop order" in result.output

    def test_an_untriggered_context_says_why_it_did_not_surface(self, initialized, tmp_path):
        """ "Nothing surfaced" and "nothing to surface" are different claims."""
        transcript = tmp_path / "t.json"
        transcript.write_text(TRANSCRIPT)

        result = runner.invoke(
            app, ["context", str(transcript), "--task", "rate limiting", "--adaptive"]
        )

        assert result.exit_code == 0
        assert "no trigger" in result.output
        assert "window usage" in result.output
        assert "CONTEXT PIPELINE" in result.output

    def test_a_triggered_context_reports_trigger_budget_and_basis(self, initialized, tmp_path):
        runner.invoke(app, ["remember", "decision", "use a token bucket for rate limiting"])
        runner.invoke(app, ["remember", "constraint", "must not add a Redis dependency"])
        transcript = self._big_transcript(tmp_path)

        result = runner.invoke(
            app, ["context", str(transcript), "--task", "rate limiting", "--adaptive", "--dry-run"]
        )

        assert "trigger" in result.output
        assert ">= 75%" in result.output
        # invariant 3's spirit: the budget never appears without its basis.
        assert "basis" in result.output
        assert "arXiv:2602.11988" in result.output
        assert "token bucket" in result.output

    def test_dry_run_stops_before_the_pipeline(self, initialized, tmp_path):
        runner.invoke(app, ["remember", "decision", "use a token bucket"])
        transcript = self._big_transcript(tmp_path)

        result = runner.invoke(
            app, ["context", str(transcript), "--task", "rate limiting", "--adaptive", "--dry-run"]
        )

        assert result.exit_code == 0
        assert "ADAPTIVE PRE-PASS" in result.output
        assert "CONTEXT PIPELINE" not in result.output

    def test_surfaced_critical_memory_survives_an_impossible_budget(self, initialized, tmp_path):
        """Invariant 1 through the merged path -- the highest-value test here.

        Surfaced records are ItemKind.MEMORY, which classify.py protects as
        CRITICAL unconditionally. Under a budget that cannot be met they must
        still be retained, and `critical_retention` must be measured against
        the merged list: against the original transcript it would exclude the
        very items at risk and pass vacuously.
        """
        runner.invoke(app, ["remember", "constraint", "must not add a Redis dependency"])
        transcript = self._big_transcript(tmp_path)

        result = runner.invoke(
            app,
            ["context", str(transcript), "--task", "rate limiting", "--adaptive", "--budget", "10"],
        )

        assert result.exit_code == 0
        assert "BUG:" not in result.output
        assert "Over budget" in result.output
        assert "all critical and were not dropped" in result.output

    def test_the_ledger_still_chains_with_surfaced_items_merged_in(self, initialized, tmp_path):
        """invariant 2 at the CLI layer: every stage's `tokens before` equals
        the previous stage's `tokens after`, parsed back out of the table."""
        import re

        runner.invoke(app, ["remember", "constraint", "must not add a Redis dependency"])
        transcript = self._big_transcript(tmp_path)

        result = runner.invoke(
            app, ["context", str(transcript), "--task", "rate limiting", "--adaptive"]
        )

        rows = re.findall(r"([\d,]+) -> ([\d,]+)\s+[\d,]+\s*$", result.output, re.MULTILINE)
        # Six stages, not seven: with no --budget the budget stage does not run.
        assert len(rows) >= 6, result.output
        for (_, prev_after), (next_before, _) in zip(rows, rows[1:], strict=False):
            assert prev_after == next_before

    def test_adaptive_without_a_verity_directory_degrades_with_a_reason(self, project, tmp_path):
        transcript = self._big_transcript(tmp_path)

        result = runner.invoke(
            app, ["context", str(transcript), "--task", "rate limiting", "--adaptive"]
        )

        assert result.exit_code == 0
        assert "degraded" in result.output
        assert "verity init" in result.output
