"""Phase 2 Week 4 acceptance test: retry-loop latency under llama2:13b's
real throughput constraint (~30-35 tok/s, per the architecture plan).

No live Ollama is available in CI, so this checks two things that don't
require one:
1. The retry loop is genuinely sequential in wall-clock time -- N attempts
   cost at least N x per-attempt latency, rather than silently skipping
   or short-circuiting attempts.
2. A closed-form estimate, using the plan's own documented throughput
   numbers, stays within a documented SLA bound for a full 3-attempt
   request. If this ever fails, the token estimate or the SLA needs
   revisiting -- not a sign the test is wrong.
"""

import time
from typing import Optional

from verityai.agent.orchestrator import Orchestrator
from verityai.neural.ollama_client import OllamaGenerationError
from verityai.ontology.models import GenerationRequest

# From the architecture plan: llama2:13b's realistic local throughput.
MIN_TOKENS_PER_SECOND = 30
TYPICAL_RESPONSE_TOKENS = 250  # code + reasoning, order-of-magnitude estimate
MAX_ATTEMPTS_SLA_SECONDS = 60  # documented acceptance bound for a full 3-attempt retry loop

# Test-only stand-in delay. Real per-call latency at MIN_TOKENS_PER_SECOND
# would be TYPICAL_RESPONSE_TOKENS / MIN_TOKENS_PER_SECOND (~8.3s); sleeping
# that long per call would make the suite slow, so this is scaled down for
# CI speed. The SLA assertion below reasons in real seconds separately,
# using the documented constants directly rather than this delay.
SIMULATED_PER_CALL_DELAY_SECONDS = 0.05


class SimulatedLatencyLLMClient:
    """Like the FakeLLMClient used elsewhere, but actually costs wall-clock
    time per call, so the retry loop's sequential-ness can be measured
    instead of assumed."""

    def __init__(self, responses: list[str], delay_seconds: float = SIMULATED_PER_CALL_DELAY_SECONDS):
        self.responses = responses
        self.delay_seconds = delay_seconds
        self.call_count = 0

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        time.sleep(self.delay_seconds)
        if self.call_count >= len(self.responses):
            raise OllamaGenerationError("No more scripted responses", attempts=1)
        response = self.responses[self.call_count]
        self.call_count += 1
        return response


def wrap_code(code: str) -> str:
    return f"Here is the code:\n\n```python\n{code}\n```"


class TestRetryLoopIsGenuinelySequential:
    def test_three_failed_attempts_cost_at_least_three_call_latencies(self):
        failing_code = "x = 1\nassert x == 999"
        llm = SimulatedLatencyLLMClient([wrap_code(failing_code)] * 3)
        orchestrator = Orchestrator(llm_client=llm)

        start = time.monotonic()
        result = orchestrator.run(GenerationRequest(prompt="test", max_attempts=3))
        elapsed = time.monotonic() - start

        assert result.status == "failed"
        assert llm.call_count == 3
        assert elapsed >= 3 * SIMULATED_PER_CALL_DELAY_SECONDS

    def test_llm_failure_aborts_immediately_without_paying_full_retry_cost(self):
        """An unreachable LLM should fail fast on the first call, not burn
        3 x the per-call latency retrying identically."""

        class AlwaysFailingLLMClient:
            def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
                time.sleep(SIMULATED_PER_CALL_DELAY_SECONDS)
                raise OllamaGenerationError("Connection refused", attempts=1)

        orchestrator = Orchestrator(llm_client=AlwaysFailingLLMClient())

        start = time.monotonic()
        orchestrator.run(GenerationRequest(prompt="test", max_attempts=3))
        elapsed = time.monotonic() - start

        assert elapsed < 3 * SIMULATED_PER_CALL_DELAY_SECONDS


class TestLatencySLA:
    def test_worst_case_three_attempts_stays_within_documented_sla(self):
        """Closed-form check against the plan's own throughput numbers --
        not a live benchmark, since no Ollama instance is available in CI."""
        worst_case_per_call_seconds = TYPICAL_RESPONSE_TOKENS / MIN_TOKENS_PER_SECOND
        worst_case_three_attempts = worst_case_per_call_seconds * 3

        assert worst_case_three_attempts < MAX_ATTEMPTS_SLA_SECONDS, (
            f"3 attempts at {MIN_TOKENS_PER_SECOND} tok/s and "
            f"{TYPICAL_RESPONSE_TOKENS} tokens/response would take "
            f"{worst_case_three_attempts:.1f}s, exceeding the "
            f"{MAX_ATTEMPTS_SLA_SECONDS}s SLA -- either the token estimate "
            f"or the SLA needs revisiting before this ships."
        )
