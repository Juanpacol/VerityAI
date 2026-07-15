#!/usr/bin/env python3
"""Generates real materials for T5's human-eval protocol
(docs/T5_HUMAN_EVAL_PROTOCOL.md): renders `/runs/{request_id}/view`-style
HTML for a handful of real, varied prompts, saved as static files a
recruiter can show a participant without needing the live stack running.

Real generation (llama3.2 + Neo4j hybrid retrieval + Z3), not mocked --
same infrastructure as every other real run in this project. Picks a
handful of prompts intentionally spread across likely verdict types
(simple pass, a subtle bug, something outside the verifiable subset) so
the protocol has genuine variety to show, not cherry-picked "nice" runs.

Usage:
  python scripts/generate_human_eval_materials.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from neo4j import GraphDatabase  # noqa: E402

from verityai.agent.orchestrator import Orchestrator  # noqa: E402
from verityai.api.run_view import render_run_view  # noqa: E402
from verityai.kg.client import KGClient  # noqa: E402
from verityai.neural.ollama_client import OllamaClient  # noqa: E402
from verityai.ontology.models import GenerationRequest  # noqa: E402

PROMPTS = [
    "write a function that returns the maximum of two integers",
    "write a function that safely divides two integers, returning None if the denominator is zero",
    "write a function that checks if a list of numbers contains any negative values",
    "write a function that checks array bounds before indexing into an array",
    "write a function that reverses a string",
    "write a function that returns whether a number is even, using bitwise operations",
]


def main() -> int:
    repo_root = Path(__file__).parent.parent
    output_dir = repo_root / "docs" / "human_eval" / "materials"
    output_dir.mkdir(parents=True, exist_ok=True)

    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "neo4jpassword")
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    kg_client = KGClient(driver)
    llm_client = OllamaClient(
        model=model, base_url=ollama_host, embed_model=os.environ.get("OLLAMA_EMBED_MODEL")
    )
    orchestrator = Orchestrator(
        llm_client=llm_client, kg_client=kg_client, retrieval_strategy="hybrid"
    )

    manifest = []
    for i, prompt in enumerate(PROMPTS, start=1):
        print(f"[{i}/{len(PROMPTS)}] {prompt}")
        response = orchestrator.run(GenerationRequest(prompt=prompt, max_attempts=3))
        html = render_run_view(response.traces)

        file_name = f"sample_{i:02d}.html"
        (output_dir / file_name).write_text(html)

        manifest.append(
            {
                "index": i,
                "prompt": prompt,
                "request_id": str(response.request_id),
                "status": response.status,
                "final_verification_status": response.final_verification.status.value,
                "confidence": response.confidence,
                "attempts": len(response.traces),
                "file": file_name,
            }
        )
        print(
            f"    -> {response.final_verification.status.value}, "
            f"conf={response.confidence:.2f}, attempts={len(response.traces)}"
        )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model": model,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "samples": manifest,
            },
            indent=2,
        )
    )
    print(f"\nWrote {len(manifest)} samples + manifest to {output_dir}")

    llm_client.close()
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
