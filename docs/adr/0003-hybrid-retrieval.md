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
not thousands) and there is no local embedding model beyond whatever's
already pulled via Ollama (`llama3.2`, optionally `qwen3:8b` — see
`docs/PHASE_3_METHODOLOGY.md`'s cross-model hardware findings). The Ollama
container in `docker/docker-compose.yml` cannot resolve DNS to pull a
dedicated embedding model (`nomic-embed-text` etc.); only the host can reach
the network. So any embedding-based retrieval has to work with an
LLM-as-embedder of mediocre quality, or not exist yet.

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

### `llama3.2` as the embedder — a real caveat, not hidden

There is no dedicated embedding model available locally (`docker exec
ollama wget --spider https://registry.ollama.ai` fails — no DNS in the
container; the host can reach the network but pulling a new model into the
container from the host isn't wired up). `OllamaClient.embed()` (Commit 1)
works with any Ollama-served model via `POST /api/embed`, including
`llama3.2` — a generation model, not one trained for embeddings. It produces
*some* usable vector, but the semantic ranking quality is expected to be
mediocre compared to a dedicated embedding model. This is why:

- `RetrievalResult.mode` and `degraded_reason` always report whether
  semantic scoring actually ran, and `embedding_model` is stored per-rule
  (`kg/ingestion.py`'s `set_rule_embedding`) so results are auditable.
- The retrieval A/B run (Commit 7, `docs/PHASE_3_METHODOLOGY.md` "Real run
  #3") reports whether the observed mode was `hybrid` or `lexical_only` in
  practice, and characterizes quality honestly rather than assuming the
  hybrid arm is better just because it's more sophisticated.
- `OLLAMA_EMBED_MODEL` is a separate config knob from the generation model,
  so swapping in a real embedding model later (once one can be pulled) is a
  one-line config change, not a code change.

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

- Retrieval quality is bounded by `llama3.2`'s embedding quality until a
  dedicated embedding model is available — an honest, documented
  limitation, not a hidden one.
- `Rule` nodes gain two new properties (`embedding`, `embedding_model`),
  additive and optional — `get_rules_with_embeddings` returns `None` for
  either, and no existing query or test that doesn't ask for them is
  affected.
- Retrieval becomes a genuine two-axis system (lexical relevance + semantic
  relevance) with per-result provenance, which is also what makes the
  reasoning-trace visual view (see the sibling commits in this branch)
  worth building — there's now something explainable to show.
