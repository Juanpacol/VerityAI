"""LLM-based relevance/claim classification for evidence records.

Mirrors `kg/retrieval.py`'s `embed_fn` injection pattern exactly:
`EvidenceClassifier` takes a `generate_fn: Optional[Callable[[str], str]]`
rather than constructing an `OllamaClient` itself, so `evidence/` never
imports `neural/` (CLAUDE.md's module dependency rule). The wiring layer
(`scripts/classify_evidence.py`) is what injects `OllamaClient(...).generate`.

Every degradation path (no generate_fn, generate_fn raises, unparseable
response) is stamped with a `degraded_reason` and never silently treated
as "classified" -- same philosophy as `RetrievalResult.degraded_reason`
and the `NOT_VERIFIED` status.
"""

import json
import re
from typing import Any, Callable, Optional

from verityai.evidence.models import Classification, EvidenceRecord, ResearchTopic

_TOPICS: tuple[ResearchTopic, ...] = ("T1", "T2", "T3", "T4", "T5", "T6")
_MAX_CONTENT_CHARS = 2000
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_TOPIC_DESCRIPTIONS = """\
T1: confidence calibration -- does a confidence score predict actual correctness?
T2: retry-loop / self-correction trade-offs in LLM code generation
T3: what fraction of real code is inside a narrow formally-verifiable subset
T4: how many rules/patterns comparable tools ship (corpus size ROI)
T5: what builds developer trust in AI-generated code
T6: can SMT/formal methods detect SQL injection or race conditions at all\
"""


def _build_prompt(record: EvidenceRecord) -> str:
    content_summary = json.dumps(record.content, indent=2)[:_MAX_CONTENT_CHARS]
    return f"""You are assessing a piece of research evidence for relevance to six
research topics (T1-T6) in a code-verification research project.

Research topics:
{_TOPIC_DESCRIPTIONS}

Evidence source: {record.source}
Evidence content (may be truncated):
{content_summary}

Respond with STRICT JSON only -- no markdown code fences, no commentary
before or after -- in exactly this shape:
{{"relevance": {{"T1": 0.0, "T2": 0.0, "T3": 0.0, "T4": 0.0, "T5": 0.0, "T6": 0.0}}, "extracted_claims": ["claim 1", "claim 2"], "confidence": 0.0}}

relevance values are 0.0-1.0 (how relevant this evidence is to each topic).
extracted_claims is up to 3 short factual claims this evidence supports.
confidence is your own 0.0-1.0 confidence in this classification."""


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip()).strip()


def _clamp01(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, num))


def _parse_response(raw: str) -> Optional[dict]:
    try:
        parsed = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    relevance_raw = parsed.get("relevance", {})
    if not isinstance(relevance_raw, dict):
        return None
    relevance = {topic: _clamp01(relevance_raw.get(topic, 0.0)) for topic in _TOPICS}

    claims_raw = parsed.get("extracted_claims", [])
    claims = [str(c) for c in claims_raw][:3] if isinstance(claims_raw, list) else []

    return {
        "relevance": relevance,
        "extracted_claims": claims,
        "confidence": _clamp01(parsed.get("confidence", 0.0)),
    }


class EvidenceClassifier:
    """Classifies an `EvidenceRecord`'s relevance to T1-T6 via an injected
    `generate_fn`. `generate_fn=None` (the default) always degrades --
    callers must explicitly opt in to LLM classification.
    """

    def __init__(
        self,
        generate_fn: Optional[Callable[[str], str]] = None,
        model_name: str = "unknown",
    ):
        self.generate_fn = generate_fn
        self.model_name = model_name

    def classify(self, record: EvidenceRecord) -> Classification:
        if self.generate_fn is None:
            return Classification(
                classified_by="none",
                degraded_reason="no generate_fn configured",
            )

        prompt = _build_prompt(record)
        try:
            raw = self.generate_fn(prompt)
        except Exception as e:  # noqa: BLE001 -- degrade, don't crash the caller
            return Classification(
                classified_by=self.model_name,
                degraded_reason=f"generate_fn raised: {e}",
            )

        parsed = _parse_response(raw)
        if parsed is None:
            return Classification(
                classified_by=self.model_name,
                degraded_reason="unparseable_llm_response",
                extracted_claims=[raw[:500]] if raw else [],
            )

        return Classification(
            classified_by=self.model_name,
            relevance=parsed["relevance"],
            extracted_claims=parsed["extracted_claims"],
            confidence=parsed["confidence"],
            classification_reviewed=False,
        )
