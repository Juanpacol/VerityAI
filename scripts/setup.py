#!/usr/bin/env python3
"""
Phase 0 Setup Script — Validation of reproducible environment.

This script is the Phase 0 deliverable acceptance criterion:
1. Docker services (Neo4j, Postgres, Redis, Ollama) are healthy
2. Ollama can generate code with llama2:13b
3. Code can be saved to and read from Neo4j
4. All communication works end-to-end
"""

import os
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import requests
from neo4j import GraphDatabase
from verityai.neural.ollama_client import OllamaClient


def check_service_health(service_name: str, url: str, max_retries: int = 10) -> bool:
    """Check if a service is healthy."""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                print(f"✓ {service_name} is healthy")
                return True
        except Exception:
            pass
        time.sleep(1)
    print(f"✗ {service_name} failed health check")
    return False


def test_ollama() -> bool:
    """Test Ollama LLM generation."""
    print("\n=== Testing Ollama ===")

    if not OllamaClient.is_available():
        print("✗ Ollama server not available at http://localhost:11434")
        return False
    print("✓ Ollama server is available")

    try:
        client = OllamaClient(model="llama2:13b")
        print("Generating code with llama2:13b...")

        prompt = """Generate a simple Python function that returns the sum of two numbers.
Keep it short (3-5 lines).
Just the code, no explanation."""

        result = client.generate(prompt)
        print(f"✓ Generated code:\n{result[:200]}...")
        return True
    except Exception as e:
        print(f"✗ Failed to generate code: {e}")
        return False


def test_neo4j() -> bool:
    """Test Neo4j connectivity and basic operations."""
    print("\n=== Testing Neo4j ===")

    uri = "bolt://localhost:7687"
    user = "neo4j"
    password = "verityai_password_123"

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            # Test connection
            result = session.run("RETURN 'Hello, Neo4j!' as message")
            message = result.single()[0]
            print(f"✓ Connected to Neo4j: {message}")

            # Create a test pattern node
            session.run("""
                CREATE (p:Pattern {
                    name: 'test_pattern',
                    description: 'A test pattern created during Phase 0 setup',
                    language: 'python',
                    verified: true
                })
            """)
            print("✓ Created test pattern node in Neo4j")

            # Read it back
            result = session.run("""
                MATCH (p:Pattern {name: 'test_pattern'})
                RETURN p.description
            """)
            desc = result.single()[0]
            print(f"✓ Read pattern back: {desc}")

            # Cleanup
            session.run("MATCH (p:Pattern {name: 'test_pattern'}) DELETE p")
            print("✓ Cleaned up test data")

        driver.close()
        return True
    except Exception as e:
        print(f"✗ Neo4j test failed: {e}")
        return False


def main():
    """Run Phase 0 validation."""
    print("=" * 60)
    print("VerityAI — Phase 0 Setup Validation")
    print("=" * 60)

    print("\n1. Checking service health...")

    services = [
        ("Neo4j", "http://localhost:7474"),
        ("Ollama", "http://localhost:11434/api/tags"),
        ("PostgreSQL", None),  # We'll test via schema
        ("Redis", None),  # We'll test via ping
    ]

    # Health checks
    print("\nService Health Checks:")
    all_healthy = True

    neo4j_ok = check_service_health("Neo4j", "http://localhost:7474")
    all_healthy &= neo4j_ok

    ollama_ok = check_service_health("Ollama", "http://localhost:11434/api/tags", max_retries=15)
    all_healthy &= ollama_ok

    # PostgreSQL check
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="localhost",
            database="verityai_db",
            user="verityai_user",
            password="verityai_pass_123",
            connect_timeout=5,
        )
        conn.close()
        print("✓ PostgreSQL is healthy")
    except Exception:
        print("✗ PostgreSQL health check failed")
        all_healthy = False

    # Redis check
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, socket_connect_timeout=2)
        r.ping()
        print("✓ Redis is healthy")
    except Exception:
        print("✗ Redis health check failed")
        all_healthy = False

    if not all_healthy:
        print("\n⚠ Some services are not healthy. Run:")
        print("  docker-compose -f docker/docker-compose.yml up -d")
        print("  docker-compose -f docker/docker-compose.yml logs -f")
        return 1

    # Functional tests
    print("\n2. Running functional tests...")

    ollama_test = test_ollama()
    neo4j_test = test_neo4j()

    # Summary
    print("\n" + "=" * 60)
    if all_healthy and ollama_test and neo4j_test:
        print("✓ Phase 0 validation PASSED")
        print("\nVerityAI environment is ready for Phase 1 development.")
        print("=" * 60)
        return 0
    else:
        print("✗ Phase 0 validation FAILED")
        print("\nFix the issues above and run this script again.")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
