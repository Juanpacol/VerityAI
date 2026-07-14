"""Unit tests for cli/verityai_cli.py using Typer's CliRunner.

`verify`/`explain` never touch the LLM (they call verify_python_snippet
directly on a file), so those run with no mocking at all. `generate` needs
_build_orchestrator monkeypatched to a FakeLLMClient-backed factory --
otherwise it would try to reach a live Ollama server.
"""

from pathlib import Path
from typing import Optional

from typer.testing import CliRunner

from verityai.agent.orchestrator import Orchestrator
from verityai.cli import verityai_cli
from verityai.cli.verityai_cli import app
from verityai.neural.ollama_client import OllamaGenerationError

runner = CliRunner()


class FakeLLMClient:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.call_count = 0

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if self.call_count >= len(self.responses):
            raise OllamaGenerationError("No more scripted responses", attempts=1)
        response = self.responses[self.call_count]
        self.call_count += 1
        return response


def wrap_code(code: str) -> str:
    return f"```python\n{code}\n```"


class TestGenerateCommand:
    def test_successful_generation_exits_zero_and_prints_code(self, monkeypatch):
        monkeypatch.setattr(
            verityai_cli,
            "_build_orchestrator",
            lambda model, ollama_host: Orchestrator(
                llm_client=FakeLLMClient([wrap_code("x = 1\nassert x == 1")])
            ),
        )

        result = runner.invoke(app, ["generate", "assign 1 to x"])

        assert result.exit_code == 0
        assert "x = 1" in result.stdout
        assert "success" in result.stdout.lower()

    def test_failed_verification_exits_nonzero(self, monkeypatch):
        monkeypatch.setattr(
            verityai_cli,
            "_build_orchestrator",
            lambda model, ollama_host: Orchestrator(
                llm_client=FakeLLMClient([wrap_code("x = 1\nassert x == 999")] * 3)
            ),
        )

        result = runner.invoke(app, ["generate", "test", "--max-attempts", "1"])

        assert result.exit_code == 1

    def test_llm_unreachable_exits_nonzero_without_crashing(self, monkeypatch):
        class AlwaysFailingLLMClient:
            def generate(self, prompt, system_prompt=None):
                raise OllamaGenerationError("Connection refused", attempts=3)

        monkeypatch.setattr(
            verityai_cli,
            "_build_orchestrator",
            lambda model, ollama_host: Orchestrator(llm_client=AlwaysFailingLLMClient()),
        )

        result = runner.invoke(app, ["generate", "test"])

        assert result.exit_code == 1


class TestVerifyCommand:
    def test_verifies_passing_file(self, tmp_path: Path):
        file = tmp_path / "ok.py"
        file.write_text("x = 1\nassert x == 1\n")

        result = runner.invoke(app, ["verify", str(file)])

        assert result.exit_code == 0
        assert "pass" in result.stdout.lower()

    def test_verifies_failing_file(self, tmp_path: Path):
        file = tmp_path / "bad.py"
        file.write_text("x = 1\nassert x == 999\n")

        result = runner.invoke(app, ["verify", str(file)])

        assert result.exit_code == 1
        assert "fail" in result.stdout.lower()

    def test_missing_file_exits_nonzero(self, tmp_path: Path):
        result = runner.invoke(app, ["verify", str(tmp_path / "nope.py")])
        assert result.exit_code == 1


class TestExplainCommand:
    def test_explains_failing_file(self, tmp_path: Path):
        file = tmp_path / "bad.py"
        file.write_text("x = 1\nassert x == 999\n")

        result = runner.invoke(app, ["explain", str(file)])

        assert result.exit_code == 0
        assert "failed" in result.stdout.lower() or "fail" in result.stdout.lower()
