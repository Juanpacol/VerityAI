# ADR-0003 — Hybrid (lexical + semantic) rule retrieval

## Status

Accepted (portfolio-differentiation phase, `feat/hybrid-retrieval-trace-view`).

## Context

`Orchestrator._fetch_kg_context` fetched all rules for two hardcoded
categories ("security" + "correctness"), regardless of what the user
actually asked for — the KG had no relevance ranking at all, exact-match
lookups by id/category only. `api/rest.py`'s `get_orchestrator` never wired
a `kg_client` into the live `/generate` endpoint in the first place, so in
practice zero KG context reached the LLM. Any retrieval improvement needed
to fix both: give the KG a real ranking signal, and actually connect it.

The corpus is small (~50 rules today, expected to grow to the low hundreds,
not thousands). Two constraints assumed at design time turned out to be
wrong once the retrieval A/B run (Commit 7) actually stood up a fresh
Ollama instance: (1) the Docker DNS failure seen against the *old*
`docker/docker-compose.yml` `ollama` container earlier in this project was
container-specific, not a property of Docker networking in general — a
freshly-created `ollama/ollama:latest` container pulled a 2GB model over
the registry with no issue; (2) `llama3.2` itself turned out to be unusable
as an embedder against the Ollama version actually available
(`/api/embed` returns HTTP 501 `"This server does not support embeddings"`
for `llama3.2` specifically, regardless of flags — see the corrected
section below). Pulling a real dedicated embedding model
(`nomic-embed-text`, 274MB) was not only possible but straightforward once
attempted for real, and is what the A/B run actually used.

## Decision

### Lexical + semantic fusion via Reciprocal Rank Fusion (RRF)

`kg/retrieval.py`'s `HybridRetriever` ranks rules two ways — BM25 over
tokenized rule text, and cosine similarity over stored embeddings — then
fuses the two rankings with RRF (`score = Σ 1/(k + rank)` per scorer,
`k=60`). RRF was chosen over a weighted linear combination of raw scores
because BM25 and cosine similarity live on incomparable scales (BM25 is
unbounded and corpus-size-dependent; cosine is bounded to [-1, 1]) — fusing
by *rank* instead of raw score sidesteps needing to invent and tune a
weighting constant with no principled way to validate it against this small
a corpus.

### Non-negative BM25 IDF variant

Classic BM25 IDF (`ln((N - df + 0.5) / (df + 0.5))`) goes negative once a
term appears in more than half the corpus — with ~50 documents, common
words cross that threshold easily, and a negative IDF silently *inverts*
that term's contribution to ranking instead of just discounting it. Used
the Lucene/Okapi-style `ln(1 + (N - df + 0.5) / (df + 0.5))` variant
instead, which stays non-negative for any `df`. Verified with a regression
test (`test_retrieval.py::TestBM25::test_idf_non_negative_when_term_in_all_docs`)
using a query term present in every document in the corpus.

### In-memory scoring, not Neo4j's native vector index

Neo4j 5.x has a native vector index (`db.index.vector.*`) that would push
similarity search into the database. Deliberately **not used yet**:

- The corpus fits comfortably in memory (pure-Python BM25 + cosine over
  ~50-200 rules is sub-millisecond; no numpy dependency needed in core).
- A native vector index locks in an embedding dimension at index-creation
  time. The embedding model here is provisional (whatever Ollama model is
  locally available, see below) — committing to a fixed dimension before
  that's settled would mean an index rebuild on every model change.
- `kg/client.py`'s existing tests use a fake `Driver`/`Session` with
  plain-dict records (`tests/unit/test_kg_client.py`) precisely so KG logic
  is testable without a live Neo4j instance. A native vector index query
  would need either a real Neo4j instance in tests or a much more elaborate
  fake — not justified yet at this corpus size.

This is documented as the scaling path, not ruled out: if the rule corpus
grows into the thousands, or query latency on the in-memory path becomes a
real bottleneck, migrating `HybridRetriever`'s semantic half to a Neo4j
vector index is the natural next step, with the embedding dimension
question resolved by then (a settled `OLLAMA_EMBED_MODEL`, or a dedicated
embedding model once Docker's Ollama container can pull one).

### `llama3.2` cannot serve embeddings; `nomic-embed-text` can — corrected in place

**This ADR originally assumed `llama3.2` would serve as a mediocre-but-usable
embedder, since no dedicated embedding model could be pulled.** Both halves
of that assumption turned out to be wrong once the Commit 7 A/B run stood
up a real Ollama instance to test against:

- `POST /api/embed` against `llama3.2` returns HTTP 501:
  `"This server does not support embeddings. Start it with --embeddings"`
  — on Ollama 0.30.11, this is not a flag/config issue (`ollama serve
  --help` lists no such flag on this version) but an actual per-model
  restriction; it reproduced identically across two independent
  containers, so it is llama3.2-specific, not an environment fluke.
- Pulling `nomic-embed-text` (274MB, a real embedding model) worked without
  any special configuration and produces real 768-dim embeddings via the
  same `POST /api/embed` call.

Net effect: the "mediocre LLM-as-embedder" scenario this ADR was written
to accommodate never actually had to be used. `OLLAMA_EMBED_MODEL` — kept
as a separate config knob from the generation model specifically so this
kind of swap would be a config change, not a code change — is set to
`nomic-embed-text` for the real A/B run. The degradation ladder this ADR
already designed for (no embed_fn / embed_fn raises / no stored
embeddings → `lexical_only`, always reported in provenance) is unchanged
and still applies verbatim if `OLLAMA_EMBED_MODEL` is ever pointed back at
a generation-only model like `llama3.2`, or if no embedding model is
available at all in some other deployment:

- `RetrievalResult.mode` and `degraded_reason` always report whether
  semantic scoring actually ran, and `embedding_model` is stored per-rule
  (`kg/ingestion.py`'s `set_rule_embedding`) so results are auditable.
- The retrieval A/B run (Commit 7, `docs/PHASE_3_METHODOLOGY.md` "Real run
  #3") reports whether the observed mode was `hybrid` or `lexical_only` in
  practice, and characterizes quality honestly rather than assuming the
  hybrid arm is better just because it's more sophisticated.

### `embed_fn` injection, not a `kg/` → `neural/` import

`HybridRetriever.__init__` takes `embed_fn: Optional[Callable[[str],
list[float]]] = None` rather than constructing an `OllamaClient` itself.
This preserves the module dependency rule in `CLAUDE.md`: `kg/` has zero
dependency on `neural/`, breaking what would otherwise be a real coupling
between the KG and whatever LLM client happens to be in use. The `agent/`
orchestration layer (which already depends on both) is responsible for
wiring `orchestrator.llm_client.embed` in as the `embed_fn` — see Commit 4.
It also means `kg/retrieval.py` is fully testable with a plain Python
callable standing in for `embed_fn`, no Ollama mocking required
(`tests/unit/test_retrieval.py`).

## Consequences

- Retrieval quality depends on `OLLAMA_EMBED_MODEL` being set to a real
  embedding model (`nomic-embed-text`, confirmed working) rather than a
  generation-only model like `llama3.2` (confirmed non-functional for
  embeddings on the Ollama version tested) — a config requirement now
  documented from an actual test, not an assumption.
- `Rule` nodes gain two new properties (`embedding`, `embedding_model`),
  additive and optional — `get_rules_with_embeddings` returns `None` for
  either, and no existing query or test that doesn't ask for them is
  affected.
- Retrieval becomes a genuine two-axis system (lexical relevance + semantic
  relevance) with per-result provenance, which is also what makes the
  reasoning-trace visual view (see the sibling commits in this branch)
  worth building — there's now something explainable to show.
