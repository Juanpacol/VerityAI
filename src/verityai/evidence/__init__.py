"""External evidence pipeline: fetch, store, validate, and classify research
evidence backing the T1-T6 research roadmap (see docs/RESEARCH_FINDINGS.md
once it exists, and the plan in .claude's tracked history).

Deliberately depends on `ontology`, `symbolic`, and the stdlib (+`requests`)
only -- never `neural/` or `kg/` -- per the module dependency rule in
CLAUDE.md. LLM classification is injected as a callable (see
`evidence.classify.EvidenceClassifier`), mirroring `kg/retrieval.py`'s
`embed_fn` pattern.
"""

from verityai.evidence.models import Classification, EvidenceRecord, ValidationReport
from verityai.evidence.store import EvidenceStore

__all__ = ["EvidenceRecord", "ValidationReport", "Classification", "EvidenceStore"]
