"""VerityAI CLI: `verityai generate|verify|explain`.

`generate` needs a live Ollama instance (it runs the full generate-verify-
retry loop); `verify`/`explain` only need a Python file and never touch the
LLM, since they call verify_python_snippet directly.
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax

from verityai.agent.orchestrator import Orchestrator
from verityai.neural.ollama_client import OllamaClient, OllamaGenerationError
from verityai.ontology.models import GenerationRequest
from verityai.symbolic.debugger import SymbolicDebugger
from verityai.symbolic.verify import verify_python_snippet

app = typer.Typer(
    help="VerityAI: neuro-symbolic code generation + formal verification",
    no_args_is_help=True,
)
console = Console()


def _build_orchestrator(model: str, ollama_host: str) -> Orchestrator:
    """Construct an Orchestrator against a live Ollama instance.

    Its own module-level function (rather than inlined in `generate`) so
    tests can monkeypatch it with a FakeLLMClient-backed factory instead of
    needing a real Ollama server.
    """
    llm_client = OllamaClient(model=model, base_url=ollama_host)
    return Orchestrator(llm_client=llm_client)


@app.command()
def generate(
    prompt: str,
    language: str = typer.Option("python", help="Target language"),
    max_attempts: int = typer.Option(3, help="Max retry attempts"),
    model: str = typer.Option("llama3.2", help="Ollama model name"),
    ollama_host: str = typer.Option("http://localhost:11434", help="Ollama server URL"),
) -> None:
    """Generate code from a prompt, verify it, and print the result."""
    orchestrator = _build_orchestrator(model, ollama_host)

    try:
        response = orchestrator.run(
            GenerationRequest(prompt=prompt, language=language, max_attempts=max_attempts)
        )
    except OllamaGenerationError as e:
        console.print(f"[bold red]Generation failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    if response.code:
        console.print(Syntax(response.code, language, theme="monokai", line_numbers=True))

    console.print(f"\n[bold]Status:[/bold] {response.status}")
    console.print(f"[bold]Confidence:[/bold] {response.confidence:.1%}")
    console.print(f"\n{response.explanation}")

    if response.status != "success":
        raise typer.Exit(code=1)


@app.command()
def verify(file: Path) -> None:
    """Verify a Python file's internal consistency (its own asserts) via Z3."""
    if not file.exists():
        console.print(f"[bold red]File not found:[/bold red] {file}")
        raise typer.Exit(code=1)

    code = file.read_text()
    result = verify_python_snippet(code)
    debugger = SymbolicDebugger(code)

    console.print(f"[bold]Status:[/bold] {result.status.value}")
    console.print(f"[bold]Confidence:[/bold] {result.confidence:.1%}")
    console.print(debugger.explain_failure(result))

    if result.status.value != "pass":
        raise typer.Exit(code=1)


@app.command()
def explain(file: Path) -> None:
    """Verify a Python file and print a detailed human-readable explanation."""
    if not file.exists():
        console.print(f"[bold red]File not found:[/bold red] {file}")
        raise typer.Exit(code=1)

    code = file.read_text()
    result = verify_python_snippet(code)
    debugger = SymbolicDebugger(code)
    console.print(debugger.explain_failure(result))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
