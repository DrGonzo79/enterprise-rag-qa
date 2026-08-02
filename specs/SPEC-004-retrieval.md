# SPEC-004 — Hybrid Retrieval

**Status:** Approved — 2026-07-26 (review amendments: ef_search application specified and tested, filters pushed into both branch queries with `doc_type` support, degenerate-input ACs, breadcrumb↔FTS coupling note, AC-6 floor replaced by baseline measurement). **Amended 2026-07-26 post-implementation:** Key decision 12 restated — the measurement is underpowered and the citation result is noise, not evidence; new Key decision 12a makes corpus de-saturation a prerequisite for SPEC-007; AC-8 gains a binding SPEC-007 note on the query-embedding cache. **Second review, same day:** new AC-6a declares the tuning metric (primary `recall@8`, diagnostic `MRR@8`) that SPEC-003's de-saturation gate is assessed against, and adds a binding SPEC-007 note scoping a **retrieval-only evaluation set** separate from the golden set; a withdrawn claim about corpus size and decision rates is corrected in KD-12a. **Third review, 2026-07-26:** new Key decision 14 and AC-13 gate baseline production behind an explicit flag with a guard that fails any run writing one — a routine `pytest` overwrote a baseline artifact and it survived only because it was noticed; AC-8 gains a binding SPEC-007 note that the *published* latency figure is retrieval-side. **Amendment 4 — 2026-08-02, owner-approved:** AC-8's end-to-end **p50 assertion is withdrawn** and end-to-end latency is recorded only; the degraded-provider window it was accidentally detecting moves to a production series (`rag_qa_embed_latency_seconds`) with an `absent()` pair, and the 20× shed-threshold consequence is written into SPEC-006 Key decision 10.
**Date:** 2026-07-25
**Depends on:** SPEC-003

## Purpose

Turn a natural-language question into the k best chunks with citation metadata: embed the query, run dense vector search (pgvector HNSW, cosine) and Postgres full-text search (tsvector) **concurrently** (SPEC-002 Key decision 5), fuse with Reciprocal Rank Fusion, pass through a (stubbed) reranker seam, and return `RetrievedChunk` records carrying `section_path` so answers can cite *where*.

Two correctness obligations are built in from the start, not retrofitted:

1. **Embedder identity is verified at query time.** The stored corpus and the query embedder must be the same provider+model, or `retrieve()` raises — a fake-embedder corpus or a wrong-model query must fail loudly on the first call, never silently degrade recall weeks later. **This spec fixes a live defect:** `pipeline.py` currently stamps `embedding_model = "text-embedding-3-small"` unconditionally, so a `--embedder fake` ingest is indistinguishable in the database from a real one. The identity string must come from the embedding client, not a constant.
2. **Result diversity is instrumented.** SPEC-003 Key decision 12 predicts breadcrumb-prefixed chunks may cluster by section in top-k; this spec emits distinct-sections-per-top-k on every call so SPEC-007 can measure it before anyone proposes MMR/dedup fixes.

## Non-goals

- Answer generation, prompting, or the `/ask` endpoint — retrieval is a library; HTTP wiring is the generation spec's job
- `query_log` writes — that row belongs to the answering path, which owns latency/cost/token fields retrieval cannot fill
- A real reranker (interface stubbed here; implementation is cuttable per the scope-cut ladder and would be its own spec)
- De-duplication / MMR — SPEC-003 Key decision 12 says measure first; this spec measures
- A relevance threshold / refusal signal inside `retrieve()` (see Key decision 9 — deliberate, flagged)
- Query rewriting, HyDE, multi-query expansion, conversational context
- Embedding-vector caching for repeated queries
- Re-embedding the corpus under a new model (detectable now via the identity check; the re-embed workflow is a future spec)

## Interface

### Modules

```
src/rag_qa/retrieval/
    __init__.py       # re-exports: Retriever, RetrievedChunk, RetrievalFilters,
                      #   EmbedderMismatchError, distinct_section_rate
    types.py          # RetrievedChunk, RetrievalFilters, errors
    search.py         # vector_search(), fulltext_search() — each takes its own session
    fusion.py         # rrf_fuse() — pure, no I/O
    rerank.py         # Reranker protocol + NoopReranker
    service.py        # Retriever.retrieve() — orchestration, identity check, logging
    metrics.py        # distinct_section_rate() — pure, imported by SPEC-007 later
alembic/versions/0003_*.py   # data migration: qualify embedding_model values
```

Amended in place (SPEC-003 files, same commit series):

- `ingest/embedder.py` — `EmbeddingClient` protocol gains `identity: str` (property). `OpenAIEmbeddingClient.identity = "openai:text-embedding-3-small"` (derived from its model arg); `FakeLocalEmbeddingClient.identity = "fake:sha256-v1"`.
- `ingest/pipeline.py` — writes `embedding_model=client.identity` instead of the `EMBEDDING_MODEL` constant; writes `documents.doc_type` from the parsed document.
- `ingest/types.py` — `ParsedDocument` gains `doc_type: str`; each loader sets its constant: `nist_pdf` → `"standard"`, `eurlex_html` → `"regulation"`, `edgar_10k` → `"filing"`. (Categories are semantic, not format-based — a filter for "regulatory texts vs. filings" is the plausible query; "pdf vs. html" is not.)
- `db/models.py` — `Document` gains `doc_type: Mapped[str]` (text, not null).

### Types

```python
@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: UUID
    document_id: UUID
    document_title: str
    source_uri: str
    doc_type: str  # documents.doc_type: standard | regulation | filing
    section_path: str  # breadcrumb, e.g. "EU AI Act › Chapter III › Article 6"
    ordinal: int
    text: str
    score: float  # fused RRF score; post-rerank score once a real reranker exists
    vector_rank: int | None  # 1-based rank in the dense list; None if absent from it
    fulltext_rank: int | None  # 1-based rank in the FTS list; None if absent from it


@dataclass(frozen=True)
class RetrievalFilters:
    document_ids: tuple[UUID, ...] | None = None
    source_uris: tuple[str, ...] | None = None
    doc_types: tuple[str, ...] | None = None  # documents.doc_type (added by migration 0003)


class EmbedderMismatchError(RuntimeError): ...  # message names both identities


class EmptyCorpusError(RuntimeError): ...


class Retriever:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        query_embedder: EmbeddingClient,  # SPEC-003 protocol, now with .identity
        reranker: Reranker = NoopReranker(),
    ) -> None: ...

    async def retrieve(
        self, query: str, k: int = 8, filters: RetrievalFilters | None = None
    ) -> list[RetrievedChunk]: ...
```

### Constants (module-level in `service.py` / `fusion.py`; deliberately not config — see Key decision 8)

| Constant | Value | Role |
|---|---|---|
| `CANDIDATE_POOL` | 50 | rows fetched from *each* search before fusion |
| `RRF_K` | 60 | RRF smoothing constant (Key decision 2) |
| `RERANK_WINDOW` | `4 * k` | fused candidates handed to the reranker |

### Execution flow of `retrieve()`

0. Validate input: a query that is empty or whitespace-only raises `ValueError` before any I/O (embedding an empty string is a silent garbage-in path).
1. Embed the query: `query_embedder.embed([query])` → one 1536-dim vector.
2. Open **two** sessions from the factory and `asyncio.gather` two branches (a single `AsyncSession` cannot multiplex queries — SPEC-002 Key decision 5's concurrency requires separate connections):
   - **Branch A (vector):** inside the branch's transaction, `SET LOCAL hnsw.ef_search = 50;` then
     `SELECT c.id, c.document_id, c.ordinal, c.text, c.section_path, d.title, d.source_uri, d.doc_type FROM chunks c JOIN documents d ON ... [WHERE filter] ORDER BY c.embedding <=> :qvec LIMIT 50`. `ef_search` is raised from the default 40 to match `CANDIDATE_POOL` so the index can actually return 50 candidates. **How the GUC is applied (review amendment 1):** `hnsw.ef_search` is a session GUC and the engine pools/recycles connections, so setting it once per connection is unreliable — `SET LOCAL` is issued *in the same transaction as the search, on every call*; it scopes to that transaction and reverts at commit/rollback, so no pooled connection ever carries it as leaked state. AC-11 asserts both halves (in effect during the search on a fresh pool connection; not present after release).
   - **Branch B (identity check, then FTS, sequentially on one session):**
     `SELECT DISTINCT embedding_model FROM chunks` — if zero rows → `EmptyCorpusError`; if more than one value, or the single value ≠ `query_embedder.identity` → `EmbedderMismatchError` (corpus-wide invariant: one embedder per corpus, checked regardless of filters). Then
     `SELECT ..., ts_rank_cd(c.tsv, q) AS rank FROM chunks c JOIN documents d ..., websearch_to_tsquery('english', :query) q WHERE c.tsv @@ q [AND filter] ORDER BY rank DESC LIMIT 50`.
3. Fuse: `rrf_fuse(vector_list, fts_list)` — `score(c) = Σ over lists containing c of 1 / (RRF_K + rank)`. Deterministic total order: fused score desc, then best single-list rank asc, then `chunk_id` asc. Degenerate cases are defined behavior, not errors: an empty FTS list (no lexical match — common for paraphrase queries) degrades to vector order; fewer than `k` total candidates (tiny corpus or selective filter) returns what exists, length < k, never padding and never raising.
4. Rerank seam: top `RERANK_WINDOW` fused candidates → `reranker.rerank(query, candidates, k)`. `NoopReranker` returns `candidates[:k]`.
5. Instrument: one structured log record per call — query sha256 (first 12 hex; never the raw query at INFO), `k`, result count, `distinct_section_rate`, and stage latencies `embed_ms` / `vector_ms` / `fts_ms` / `fuse_ms` / `total_ms`.

Filters (review amendment 2) are pushed into **both** branch queries as `WHERE` predicates *before* ranking and `LIMIT` — never applied to fused results after the fact, which would silently return fewer than k. Supported: `document_ids` (`c.document_id = ANY(...)`), `source_uris`, and `doc_types` (both on the joined `documents` row); multiple fields AND together, values within a field OR together. AC-10 includes the push-down proof: a filtered query returns the full k when ≥ k matching chunks exist, even when the corpus-wide top hits all lie outside the filter. Note: pgvector applies predicates during/after the HNSW scan, so a highly selective filter can return fewer than `CANDIDATE_POOL` dense candidates; with a 3-document corpus and document-level filters this is acceptable and not worked around here.

### Reranker seam (`rerank.py`)

```python
class Reranker(Protocol):
    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], k: int
    ) -> list[RetrievedChunk]: ...


class NoopReranker:
    async def rerank(self, query, candidates, k):  # candidates arrive fused-order
        return candidates[:k]
```

The seam's position (post-fusion, pre-truncation, window `4k`) is fixed now so a future cross-encoder reranker changes zero call sites. Per the scope-cut ladder the real implementation is the second thing cut; the protocol costs ~10 lines and prevents a retrofit through every caller.

### Diversity metric (`metrics.py`)

```python
def distinct_section_rate(chunks: Sequence[RetrievedChunk]) -> float:
    """len(unique section_path) / len(chunks); 0.0 for empty input."""
```

Pure function, no I/O — `retrieve()` calls it for the per-query log line, and SPEC-007 imports the *same* function for eval aggregation, so the number in production logs and the number in eval reports can never diverge by construction.

### Migration `0003`

Two changes, one migration (this spec's single schema-touching migration per SPEC-002's rule):

1. **Qualify embedder identities (data):** `UPDATE chunks SET embedding_model = 'openai:' || embedding_model WHERE embedding_model NOT LIKE '%:%'`. Downgrade strips the `openai:` prefix. **Known limitation, accepted:** rows previously ingested with the fake embedder are mislabeled `text-embedding-3-small` (the pipeline.py defect) and the migration cannot distinguish them; any database known to contain fake-embedder rows must be re-ingested. The dev `rag` database holds the real corpus (real embeddings), so the migration is correct there; test databases are rebuilt from scratch anyway.
2. **Add `documents.doc_type text not null` (schema, review amendment 2):** added nullable, backfilled by `source_uri` pattern (`nvlpubs.nist.gov` → `standard`; `eur-lex`/`publications.europa.eu` → `regulation`; `sec.gov` → `filing`; anything else → `unknown`), then set not-null. Downgrade drops the column. Backfill-by-URI is a one-time bridge for pre-0003 rows; all new rows get `doc_type` from the loader.

### New dependencies

None. Query embedding reuses `openai` (SPEC-003); everything else is SQLAlchemy + stdlib.

## Key decisions

1. **RRF, not score fusion.** Cosine distance and `ts_rank_cd` live on incommensurable scales; min-max normalizing them per query makes each chunk's score depend on which other chunks happened to be retrieved — unstable and untestable. RRF consumes only ranks, has one parameter, and is the standard hybrid baseline. Rejected: weighted linear score combination (two tuning weights and a normalization scheme before any eval harness exists to tune against).
2. **`RRF_K = 60`.** The constant from Cormack, Clarke & Buettcher (2009), the near-universal default. Its job is damping: at k=60 the gap between rank 1 (1/61) and rank 2 (1/62) is small, so one list can't dominate on rank-1 alone, while top ranks still outweigh the tail. With exactly two lists and a 50-candidate pool, sensitivity to k is low; tuning it before SPEC-007 can measure the effect is unfalsifiable knob-twiddling. Revisit only with eval data.
3. **Embedder identity is a single qualified string (`provider:model`) in the existing `embedding_model` column** — `openai:text-embedding-3-small`, `fake:sha256-v1`. **Flagged — arguing against the obvious choice:** the obvious design is a separate `embedding_provider` column (normalized, queryable). Argued down because the only operation ever performed on this value is equality comparison against the query embedder's identity; two columns mean two things to compare, two things to backfill, and a schema migration — for a value that is semantically one opaque identifier. If a future spec needs provider-level queries, splitting is a cheap data migration then.
4. **Identity verified on every `retrieve()` call, not at startup.** **Flagged — arguing against the obvious choice:** a startup check is the obvious, cheaper design. But the ingestion CLI can rewrite the corpus while the API process is running (whole-document replace, SPEC-003 decision 6) — a startup check validates a corpus that may no longer exist. The per-call check is one `SELECT DISTINCT` on an indexed-scan-friendly small table, and it runs inside the `gather` alongside the searches (Branch B, before FTS), so it adds **zero wall-clock latency** in the common case. Mismatch is an exception, never a warning: a warning in a log nobody tails *is* the silent-degradation failure mode.
5. **Two sessions per call, identity check sharing the FTS session.** `gather` needs ≥ 2 connections (one `AsyncSession` = one connection = sequential). Three parallel sessions would buy ~1 ms on the cheapest query while tripling per-request connection pressure against SPEC-002 decision 8's hard pool bound of 10 per replica; at 2 connections per in-flight retrieve, 5 concurrent retrievals saturate a replica's pool — acceptable for a demo service, and the answering spec inherits this math explicitly.
6. **`websearch_to_tsquery`, not `to_tsquery`/`plainto_tsquery`.** It never raises on arbitrary user input (`to_tsquery` throws on unbalanced syntax — a user typing `6(2` must not 500), and it preserves quoted-phrase support that `plainto_tsquery` lacks — useful for exact citations.
7. **`CANDIDATE_POOL = 50` per list, `ef_search` raised to match.** Deep enough that RRF can promote a chunk ranked ~40th in one list and unranked in the other; shallow enough that both queries stay ms-scale on a corpus of a few thousand chunks. `SET LOCAL hnsw.ef_search = 50` because the default (40) would silently cap the dense list below the requested `LIMIT`.
8. **Retrieval constants are module constants, not `IngestConfig`-style config.** Chunking parameters went into a frozen config because they feed `content_hash`; retrieval parameters affect no stored state and have no idempotency interaction. They become tunable (and worth a config object) exactly when SPEC-007 can measure a change — promoting them earlier just multiplies untested code paths.
9. **No relevance threshold in `retrieve()` — flagged, arguing against the obvious choice.** Refusal is a charter-level scored capability, so the obvious move is a min-score cutoff here ("if the best score is weak, return nothing"). Argued down: RRF scores are rank-derived and bounded (max ≈ 2/61) — they encode *agreement between lists*, not calibrated relevance, and any threshold picked now would be a guess that SPEC-007 immediately invalidates. `retrieve()` always returns its best k with scores and per-list ranks exposed; the refusal decision belongs to the generation/eval layer, made against measured score distributions.
10. **Hybrid-must-win acceptance criterion is scoped to where hybrid has a mechanism to win — flagged, arguing against the obvious choice.** The obvious AC is "hybrid beats vector-only overall." On a ~24-question smoke set, overall strict superiority is a coin flip — hybrid's edge is concentrated in exact-term queries, and a one-question swing flips the sign, making the AC flaky and inviting tuning-to-the-test. AC-6 instead requires **no regression overall** and **strict improvement on the citation-style subset** ("Article 6(2)", "Item 1A") — the precise claim in the charter's why-hybrid rationale, and the falsifiable one. **Not vindicated by measurement — corrected 2026-07-26.** An earlier revision of this decision claimed the citation result validated the scoped AC. It does not: the citation subset decides only 3 questions and splits 2–1, which is noise (Key decision 12). What measurement *did* establish is the negative half — the unscoped "hybrid beats vector-only overall" version would have failed outright. So scoping the AC was right for the reason given (an unscoped claim is a coin flip), but the scoped claim is currently **unsupported rather than supported**, and stays that way until the corpus is expanded and the comparison is re-run with enough decided questions to mean anything.
11. **Coupling note (review amendment 4): the hybrid advantage on citation queries is coupled to SPEC-003's breadcrumb prefixing (its Key decision 5).** "Article 6" matches lexically largely because the breadcrumb line (`EU AI Act › … › Article 6 …`) is prefixed into `chunks.text` and therefore into `tsv` — the article body often doesn't repeat its own number. KD-10's scoped claim (hybrid strictly beats vector-only on citation-style queries) rests on that prefix being present. **Neither decision may be tuned in isolation:** removing or reformatting breadcrumb prefixing would likely shrink or erase the FTS edge that justifies hybrid retrieval, and any such change must re-run AC-6's comparison in the same commit.
12. **MEASURED FINDING — this setup cannot distinguish hybrid from vector-only; the only result carrying weight is a paraphrase *regression* (recorded 2026-07-26, restated 2026-07-26 after review).**

    **The measurement is underpowered, and the earlier framing of it here overstated the citation result.** recall@3 and recall@8 are 1.000 for both methods on all 26 questions, so those metrics carry no information at all. Only k=1 discriminates, and there n=26 splits into very few decided questions. Counting *discordant pairs* — questions where exactly one method put the target at rank 1 — is the honest instrument:

    | subset | n | decided (discordant) | hybrid wins | vector-only wins | tied | sign-test p (2-sided) |
    |---|---|---|---|---|---|---|
    | citation | 14 | 3 | 2 (`cit-10`, `cit-13`) | 1 (`cit-12`) | 11 | 1.00 |
    | paraphrase | 12 | 4 | 0 | 4 (`par-02`, `par-06`, `par-07`, `par-08`) | 8 | 0.125 |

    - **The citation "win" is one net question out of 14** (recall@1 0.929 vs 0.857 = 13/14 vs 12/14, from a 2–1 split of three decided questions). That is a coin flip, not evidence. **KD-10's scoped claim currently rests on it, and therefore rests on nothing** — AC-6 asserts a criterion that a single question's movement would flip in either direction. The AC is retained as the *right shape* of claim, but it is not currently evidence that hybrid retrieval works, and it must not be cited as such.
    - **The paraphrase gap is the only result with weight:** 7/12 vs 11/12, and every one of the 4 decided questions favors vector-only, with zero counterexamples. Still short of conventional significance at this n (p = 0.125), but a unanimous direction is a different kind of result from a 2–1 split.

    **Mechanism (unchanged, and consistent with the direction of the paraphrase result):** RRF sees only ranks, so the full-text branch's rank-1 earns the same 1/61 as the dense branch's rank-1 *regardless of whether the lexical match means anything*. On a paraphrase query the FTS branch still returns 50 rows of incidental word overlap; a mediocre dense hit that also ranks well lexically (1/61 + 1/70 ≈ 0.031) outscores the correct dense rank-1 with no lexical match (1/61 ≈ 0.016).

    **Root cause of the saturation is corpus size, not the fusion rule (see KD-12a).** At 358 chunks, top-8 is 2.2% of the corpus and every relevant chunk reaches it by either method — there are simply not 8 plausible competitors for any query. No fusion change can be evaluated under these conditions, and neither can SPEC-007's retrieval metrics.

    **Deliberately not resolved in this spec.** The candidate fixes — weighting the branches, shortening the FTS candidate pool, gating the FTS branch on lexical-match quality — all introduce tuning parameters, and KD-1/KD-2/KD-8 commit this project to not tuning retrieval knobs before SPEC-007 can measure. Doing so on the strength of 4 decided questions would be worse than doing nothing. **Sequence: expand the corpus first (SPEC-003 amendment 2026-07-26), re-measure, then decide.** Until the corpus is expanded, the charter's "why hybrid, not vector-only" rationale is *untested*, not merely unproven.

12a. **Corpus size is the binding constraint on every retrieval metric this project reports.** A retrieval metric can only discriminate when the target chunk has enough plausible competitors to be displaced. At 358 chunks it has none, which is why recall@3 and recall@8 are pinned at 1.000 and why the k=1 comparison decides only 7 of 26 questions. This is not a property of RRF, of the embedder, or of the chunker — it is arithmetic about corpus size, and it makes **SPEC-007's eval harness unfalsifiable as currently scoped** and the Week 6 improvement loop headroom-free. Expanding the corpus is therefore a prerequisite for SPEC-007, not an enhancement; it is specified in the SPEC-003 amendment of 2026-07-26, which expands **by measured rungs until recall@8 falls below 1.000 — with no target corpus size**, since a size fixed in advance would be the same guessed-threshold error this spec removed from AC-6. **Binding on SPEC-007:** do not report retrieval metrics, tune fusion, or set quality floors against a corpus that has not passed SPEC-003 AC-10 (de-saturation). **Division of labor between the two problems, stated correctly** *(corrected second review — an earlier revision claimed corpus growth does not raise the decision rate; that was probably false and is withdrawn, since a harder corpus makes the methods disagree more often)*: corpus size is chosen for **realism and headroom**; question-set size is chosen for **statistical power**. Growing the corpus to raise decided-question counts would optimize the wrong artifact — changing the system under test to make a measurement about it easier — even though it would likely work. Power comes from the **retrieval-only eval set** (AC-6a's companion note), not from more documents and not from the 50-question golden set, whose 50 predates any power analysis.

13. **Latency is asserted tightly only where it's measurable honestly — flagged (minor).** The obvious AC is a p95 bound in CI. CI runners have noisy, shared I/O; a tight p95 there is a flaky test generator. Split instead: CI asserts the structural property (concurrency: wall time < sum of injected per-branch delays) plus a deliberately generous fake-embedder bound; the real p95 target (AC-8) runs against the real corpus locally, like SPEC-003's real-corpus tier.

14. **Measuring is a test; producing a baseline artifact is not — the write is gated behind an explicit flag, and a guard fails any run that writes one anyway** *(added 2026-07-26, third review, after a live incident)*. A routine `uv run pytest` re-ran the real-corpus tier and **overwrote `evals/retrieval_baseline.json`**. Nothing was lost — the quality metrics were byte-identical and only latency moved — but that was luck, not design: it survived because someone read the diff. **The failure mode this guards is asymmetric and irreversible.** SPEC-003 AC-13 calls these artifacts immutable and unreconstructable for a concrete reason — an artifact records a corpus state, and once the corpus advances a rung the state it described cannot be recreated. A test-suite side effect that destroys one destroys a finding, and it does so silently, in a file nobody was looking at.

    **Two mechanisms, deliberately belt-and-braces**, because a gate alone fails open the moment a future test forgets it:
    1. **The gate** — `pytest --write-baseline`. Without it the measurement still runs and every assertion still fires; only the disk write is skipped, and the measured table is printed instead. So the *test* never weakens, which matters: gating the assertions behind a flag would have quietly retired AC-6.
    2. **The guard** — the session hooks snapshot every guarded artifact by **content hash** before collection and re-check after, failing the run if anything changed. Content-hashed, not mtime-based, so rewriting identical bytes is correctly not a violation.

    **The guard is stricter than the gate, and enforces SPEC-003 AC-13 rather than merely the flag:** with `--write-baseline`, *creating* a new `evals/baselines/baseline-<N>-chunks.json` is the point and is allowed, but *modifying or deleting one that already exists* still fails — the flag authorizes writing the next rung's artifact, never rewriting a previous rung's. `evals/retrieval_baseline.json` is the explicitly-mutable most-recent-run copy and may be rewritten, but only under the flag.

    **Flagged — arguing against the obvious choice.** The obvious fix is "don't write from tests; add a `scripts/measure_baseline.py`." Rejected: the measurement needs the same fixtures, the same corpus connection, and the same assertions as the test, so a separate script duplicates all of it and then drifts — and the numbers reported would no longer be the numbers asserted. Keeping one code path and gating the *side effect* preserves the property that the published baseline is exactly what the acceptance criteria measured.

## Acceptance criteria

- **AC-1 (contract)** — Against a seeded synthetic corpus with a stub query embedder: `retrieve(q, k=8)` returns ≤ 8 `RetrievedChunk`, scores non-increasing, every field populated (non-empty `section_path`, `document_title`, `source_uri`), and repeated calls return identical ordering (deterministic tie-break).
- **AC-2 (fusion math, pure)** — `rrf_fuse` on hand-built rank lists returns exactly `1/(60+r₁) + 1/(60+r₂)` scores; a chunk present in both lists at rank 5 outscores a chunk at rank 1 in a single list iff the math says so (`1/65+1/65 > 1/61` → true, asserted numerically); tie-break order is as specified.
- **AC-3 (hybrid mechanics, synthetic)** — In a fixture where chunk T is FTS-rank-1 but outside the dense top-50, and chunk V is dense-rank-1 with no lexical match: hybrid top-8 contains both; vector-only top-8 misses T. Proves the fusion path, independent of embedding quality.
- **AC-4 (embedder identity)** —
  (a) Corpus rows say `fake:sha256-v1`, query embedder is `openai:text-embedding-3-small` → `EmbedderMismatchError` whose message contains both identities; symmetric case likewise.
  (b) Mixed identities across chunk rows → `EmbedderMismatchError`.
  (c) Matching identities → results returned.
  (d) Empty `chunks` table → `EmptyCorpusError`.
  (e) Ingest regression: a `--embedder fake` pipeline run writes `embedding_model = 'fake:sha256-v1'` on every row (the pipeline.py defect stays fixed).
- **AC-5 (migration)** — On a database with rows `embedding_model = 'text-embedding-3-small'`: `alembic upgrade head` rewrites them to `openai:text-embedding-3-small`, leaves already-qualified rows untouched (idempotent predicate), adds `documents.doc_type` not-null with URI-pattern backfill (a pre-seeded NIST-URI document row reads `standard` after upgrade); `downgrade -1` strips the prefix and drops the column; both exit 0.
- **AC-6 (retrieval quality, real corpus, local)** — A committed smoke set `evals/retrieval_smoke.jsonl` of 26 questions (14 citation-style: exact articles/items/clauses; 12 paraphrase-style), each labeled with the expected `section_path` (prefix match). Hybrid is compared against vector-only (same query embeddings, FTS branch disabled). **Measured 2026-07-26 on the 358-chunk corpus; recall@8 saturates at 1.000 for both methods, so the discriminating regime is k=1** (see Key decision 12):
  - **Asserted:** hybrid recall@1 on the citation subset **strictly greater** than vector-only (measured 0.929 vs 0.857) — KD-10's claim, in the only regime with headroom. **Currently underpowered and not evidence (KD-12):** that margin is one net question of 14, from a 2–1 split of three decided questions. The assertion is retained because it is the right *shape* of claim, but it passes on noise and must be re-run after the corpus expansion (SPEC-003 AC-10) before anyone treats a green result as validating hybrid retrieval.
  - **Asserted:** hybrid recall@8 ≥ vector-only recall@8 overall (measured 1.000 vs 1.000). This clause is *satisfied but vacuous at this corpus size* — recorded as such rather than presented as evidence.
  - **Recorded, not asserted:** recall@{1,3,8} × {overall, citation, paraphrase}, MRR@8, discordant-pair counts, the distinct-section-rate distribution, and the stage-latency split. Written to an **immutable per-corpus-state artifact** `evals/baselines/baseline-<chunk-count>-chunks.json` (SPEC-003 AC-13), with `evals/retrieval_baseline.json` retained as a copy of the most recent run. The 358-chunk measurement is preserved as `evals/baselines/baseline-358-chunks.json` before any corpus change — the before/after across corpus states is a finding, not a file to overwrite. **No absolute floor (review amendment):** the floor is set in SPEC-007 against the 50-question golden set — a provisional number amended to match the first run would be a measurement wearing a standard's clothes.
- **AC-6a (the tuning metric, declared here because SPEC-003's de-saturation gate depends on it)** *(added 2026-07-26, second review)* —
  - **Primary: `recall@8`.** Product-aligned by construction: k=8 is what reaches the generator, so evidence outside the top 8 cannot be used no matter how it is ranked. Retrieval improvements that do not change what the generator can see are not improvements to this system.
  - **Diagnostic: `MRR@8`.** Strictly more sensitive — it moves when recall@8 cannot — so it is the early-warning signal for rank-quality regressions and the instrument for comparing fusion variants that shuffle order without changing membership. It is *not* the tuning target, but it is **not** a cosmetic one either (see below).
  - **Headroom binds to the primary — and the reason is corpus realism, not MRR being worthless** *(corrected 2026-07-26, third review)*. An earlier revision claimed that rank gains under a saturated recall@8 buy only context efficiency. **That was wrong: LLM attention is position-sensitive**, so promoting evidence from rank 7 to rank 2 can change whether the model actually uses it, and therefore can move answer correctness on its own. MRR is a real quality signal, not bookkeeping. The ladder rule stands regardless, on the correct grounds: **a corpus where every relevant chunk always reaches the top 8 does not reflect the retrieval problem this system faces in use**, so tuning against it optimizes for an unrealistic regime whatever the metric. SPEC-003 AC-10 therefore escalates on saturated recall@8 even when MRR@8 has room — because the corpus is unrepresentative, not because the available gains are illusory.
  - **Binding on SPEC-007:** adopt these, or declare a different primary and **re-run SPEC-003 AC-10's de-saturation check against it** — a corpus certified de-saturated under one metric is not certified under another.
- **Cross-spec note (binding on SPEC-007) — a retrieval-only evaluation set, separate from the 50-question golden set** *(added 2026-07-26, second review)*. Key decision 12 cannot be settled with the artifacts currently planned, and the fix is more questions of a *cheaper kind*, not a bigger corpus (SPEC-003 KD-13).
  - **The 50 is a round number chosen before any power analysis existed — not a derived figure.** It must not be quietly reinterpreted as the instrument for retrieval significance simply because it is the number already written down.
  - **Why a separate set is cheap: a retrieval question needs only an expected-section label, not a verified answer.** That is the entire cost difference. Golden-set questions are expensive because correctness, groundedness, and refusal each require a human-verified answer; a retrieval question requires a human-verified *label*, which takes minutes.
  - **Semi-generated, human-verified:** sample sections from the corpus → draft a question that section answers → **a human verifies the label**. Generation drafts; a human confirms. The label is ground truth and is never machine-accepted — an auto-labeled retrieval set measures the labeler, not the retriever.
  - **Sizing is derived, not chosen:** `N ≈ required_decided_pairs / observed_decision_rate`, both measured rather than assumed. The decision rate at 358 chunks was 7/26 ≈ 0.27 overall (citation 3/14 ≈ 0.21, paraphrase 4/12 ≈ 0.33) and is **expected to rise with corpus difficulty**, so it is re-measured at the final corpus state. `required_decided_pairs` follows from the effect size SPEC-007 wants to detect; **≥ 6 is only the floor at which a *unanimous* result reaches p < 0.05**, and a realistic non-unanimous split needs materially more. **No question count is fixed here** — the inputs and the arithmetic are specified, the number is SPEC-007's to compute once the corpus is final.
  - **Division of labor:** the retrieval set measures retrieval alone (recall/MRR against section labels, no generation, no LLM judge) and is where KD-12 gets settled. The golden set stays scoped to correctness, groundedness, and refusal. Refusal questions in particular carry the whole-corpus verification and per-rung re-validation burden described in SPEC-003's golden-set note — which is exactly why they do not scale the way retrieval questions do, and why conflating the two sets would make the expensive artifact do the cheap artifact's job badly.
  - **The existing 26-question smoke set is the seed of the retrieval set**, not a third artifact to maintain.
- **AC-7 (concurrency, SPEC-002 KD-5)** — With instrumented search functions injecting `sleep(0.3)` in each branch: `retrieve()` wall time < 0.55 s (branches overlapped, not sequential), and the two branches observably use distinct connections (e.g. distinct `pg_backend_pid()`).
- **AC-8 (latency)** — Measured 2026-07-26 over the 26 smoke queries, the split is decisive: `embed_ms` p50 170 / p95 843 / max 1117, versus **retrieval-side (both branches, concurrent) p50 11 / p95 16 / max 17**. An end-to-end p95 bound is therefore a bound on OpenAI's tail latency, not on this code, and over 26 samples p95 is the second-worst call (observed run-to-run: 856, 1072, 1361 ms). Split accordingly, per the same honesty rule as Key decision 13:
  - **Asserted (local, real corpus):** retrieval-side p95 — `retrieve()` total minus the embedding round-trip — ≤ 150 ms. Currently 16 ms, ~10× headroom; this is the budget the code owns and the one a regression would move.
  - **~~Asserted (local): end-to-end p50 ≤ 800 ms~~ — WITHDRAWN.** See amendment 4 immediately below. Nothing about end-to-end latency is asserted by any test in this repository.

    **AMENDMENT 4 — APPLIED** *(2026-08-02; owner-approved in the message of 2026-08-02 selecting option (b), CLAUDE.md rule 4's owner-asked clause)*. **"A stable estimator … keeping a real end-to-end commitment" was a guarantee about a third party's latency with no stated bound, and provider weather falsified it.** Re-measured 2026-08-02 over 25 sequential single-query embeddings: **p50 192 ms, p95 483 ms — both consistent with, and p95 *better* than, the 2026-07-26 figures — but max 3680 ms against a documented max of 1117 ms (3.3×), and, separately, a multi-minute window in which p50 rose to 4517 ms (~25×) and this AC's assertion failed three consecutive times before recovering on its own.** So it was neither a stale document nor a sustained regression: the central tendency is unchanged and the *tail* is worse, with occasional sustained degraded windows the figure never claimed to exclude. Confirmed independent of any local change by re-running the same test at a stashed tree, where it also failed.

    **What changed, and what the rejected option was.** Two were put to the owner:
    - **(a) — rejected.** Keep the p50 assertion and add an explicit *skip* condition on detected provider degradation. Rejected, and it differs from (b) materially rather than cosmetically: a skip condition disarms the assertion **exactly when the provider is worst**, so the test is live only in the conditions where it cannot fail and silent in the ones a reader would want it for. It also needs a detector — a threshold on provider latency, which is the same unbounded third-party guarantee one level down, now with no test on it at all.
    - **(b) — applied.** End-to-end p50 is **recorded, never asserted**. The only latency assertion is retrieval-side p95 ≤ 150 ms — the budget this code owns, which held at **every** observation on both measurement dates (measured 16 ms, ~10× headroom).

    **What replaces the coverage, because demoting a test is not by itself a move.** The degraded window was real and the withdrawn assertion was the only place it was visible. It is now watched where degradation has consequences instead of where it has none: **`rag_qa_embed_latency_seconds`** (histogram, SPEC-006 Key decision 9), with a p95 alert at 2 s **and its `absent()` pair** — see `docs/observability.md`. 2 s is above every non-degraded observation on both dates (worst p95: 843 ms) and below the degraded-window p50 (4517 ms), so the rule fires on the condition that was detected and on nothing else. A signal traded for no signal would have been a worse trade than the one made here; this is the signal moved to where it can be acted on.

    **Operational consequence** *(recorded)*: SPEC-006 Key decision 10's semaphore is held across the embedding round-trip, and `query_acquire_timeout_seconds` is 2.0 s. At the normal embed p50 the four slots turn over in ~208 ms — roughly 19 req/s before shedding. In a degraded window at 4.5 s they turn over in ~4.5 s — **roughly 0.9 req/s, a 20× reduction in the shed threshold caused entirely by provider latency**, with the surplus becoming `overloaded` 503s. That number is now written into SPEC-006 Key decision 10 as the cost of a decision that had never been made explicitly (amendment 5 there). The SSE heartbeat is unaffected: 15 s comfortably covers a 4.5 s embed, and it exists for the generation thinking phase regardless.
  - **Recorded, not asserted:** end-to-end p50, p95 and max, and the full stage split, in `evals/retrieval_baseline.json`. Restoring **any** end-to-end assertion requires a provider-latency budget agreed separately — the sample-count argument is no longer sufficient on its own, because a stable p50 is stable only between degraded windows.
  - **Cross-spec note (binding on SPEC-007) — query-embedding cache.** Embedding is ~94% of end-to-end median latency (170 ms of 182 ms p50; 843 ms of 857 ms p95) and the golden set is re-run dozens of times during tuning, so SPEC-007 scopes a **query-embedding cache keyed on `(query, embedder_identity)`**. Three binding constraints, each a real failure mode rather than a preference:
    1. **The key must include `embedder_identity`, not just the query text.** A cache keyed on the query alone would serve vectors embedded by a different model — reintroducing, inside the cache, exactly the silent-degradation bug that KD-3/KD-4 and migration 0003 exist to eliminate. The identity check would still pass, because the *corpus* is consistent; the poisoned value is the query vector, which nothing else validates.
    2. **It must be off by default in any latency measurement.** AC-8's numbers are meaningless with a warm cache: a cached run measures a dict lookup, not retrieval. The cache must be explicitly opt-in (or bypassable per call), and the latency tier must run with it disabled.
    3. **It is an eval/dev accelerator, not a production feature.** Cache invalidation for a live service is out of scope here; a process-local or on-disk content-addressed store (sha256 of `query ‖ identity` → vector) is sufficient for re-running a fixed golden set and needs no schema change.
  - **Cross-spec note (binding on SPEC-007) — the *published* latency number is retrieval-side, not end-to-end** *(added 2026-07-26, third review)*. Re-running the quality tier on an unchanged system moved end-to-end p95 from **617 ms to 2524 ms — a 4× swing** — while retrieval-side p95 moved 13.5 ms → 13.4 ms. Nothing about the system changed; the provider's tail did. **A published figure that swings 4× on an unchanged system is worse than no figure**, because a reader cannot tell an improvement from a quiet afternoon at the provider, and neither can the person doing the improving. SPEC-007's report therefore leads with **retrieval-side latency** — the budget this code owns and the one a regression actually moves. End-to-end is reported only as a stage split (`embed_ms` / `retrieval_side_ms` / `end_to_end_ms`) with its provider-tail caveat attached, never as "the system's latency", and never as a headline in the README. This is the same honesty rule as Key decision 13, applied to publication rather than to assertion: the number that is asserted and the number that is published should be the same number.
  - **CI tier** (fake embedder, synthetic corpus ≥ 200 chunks): p95 over 50 calls ≤ 500 ms — generous by design, structural regressions only (Key decision 13).
- **AC-9 (diversity instrumentation, SPEC-003 KD-12)** — `distinct_section_rate` unit-tested (8 chunks / 3 sections → 0.375; empty → 0.0); every `retrieve()` call emits one log record containing `distinct_section_rate` and all five stage latencies (captured via `caplog`); the function is importable from `rag_qa.retrieval.metrics` without a database.
- **AC-10 (filters, push-down)** — With `filters.document_ids = (X,)`: every returned chunk has `document_id == X`; on a corpus where the top corpus-wide vector *and* FTS hits all live outside X and X holds ≥ k matching chunks, the filtered call still returns **exactly k** results (proves predicates run inside both branch queries, not post-fusion). Same shape asserted for `doc_types` and `source_uris`; combined filters AND together.
- **AC-11 (ef_search GUC, review amendment 1)** — During a `retrieve()` call on a connection drawn fresh from the pool, `SHOW hnsw.ef_search` inside the vector branch's transaction reads 50; after the call, the same pooled connection outside any retrieval transaction reads the default (40) — `SET LOCAL` scoped correctly, no leakage across pool recycling.
- **AC-12 (degenerate inputs, review amendment 3)** — (a) `retrieve("")` and `retrieve("   \n")` raise `ValueError` with **zero** embedding calls and zero SQL issued. (b) A query with no lexical match (`websearch_to_tsquery` yields no `@@` hits) returns k results in vector order without error, `fulltext_rank is None` on all. (c) A corpus/filter combination holding fewer than k chunks returns exactly that many, no padding, no error.

  **AMENDMENT 5 — APPLIED, option (i)** *(2026-08-02; owner-approved, CLAUDE.md rule 4's owner-asked clause)*. **AC-12(b) describes the branch's normal case as if it were a degenerate one. It is not degenerate — it is the majority.**

  #### The reason, which is about registers and not about conversational words

  **This corpus is three documents in three registers, and `websearch_to_tsquery` ANDs every content term. A query therefore cannot satisfy two registers at once, so a question spanning documents is close to guaranteed to return nothing.** The EU AI Act legislates in *shall*; NIST advises in *should*; the 10-K speaks in first-person *we*. Measured while authoring pilot-2: `shall` is a keyword for the AI Act and a **query-killer for every NIST chunk**, and `must` kills AI Act queries because the Act never says it.

  **That is a product defect independent of any evaluation consideration.** The system's stated purpose is retrieval-augmented Q&A *across* public compliance documents; a full-text branch that cannot match across the corpus it indexes fails the central claim, and it would fail it with a saturated eval, an unsaturated one, or no eval at all. **The conversational-word case — `"What does Article 6(2) say…"` returning zero because no chunk contains `say` — is the symptom that surfaced this, not the argument for fixing it.**

  #### What was measured

  `fulltext_search` returned **zero candidates** on 12 of 26 smoke questions (46 %) and 13 of 14 pilot-1 questions (93 %). Where it returns nothing the fused result is the vector ranking unchanged — pilot-1's top-8 was byte-identical to vector-only on 14 of 14. Verified mechanism:

  ```
  "What does Article 6(2) say about classifying high-risk AI systems?"
    -> 'articl' & '6' & '2' & 'say' & 'classifi' & 'high-risk' & 'ai' & 'system'   ->  0 hits
  "Article 6(2) high-risk"
    -> 'articl' & '6' & '2' & 'high-risk'                                          -> 33 hits
  ```

  **The branch itself is not broken** — SPEC-007 pilot-2 put 14 lexically-anchored questions through it and **0 of 14** returned zero candidates. The mechanism works; its operating envelope was far narrower than the architecture assumed.

  #### The change

  **When the `websearch_to_tsquery` conjunction returns zero rows, re-issue the same search with the query's lexemes OR-ed together.** The fallback runs **only** where the branch currently contributes nothing, so **no query that works today changes**. Same session, same filters, same ranking, same candidate pool — one extra round trip on the fallback path only.

  **Rejected alternatives, recorded so they are visibly rejected rather than quietly untried:**
  - **(ii) a stop-list of conversational tokens.** The list is unbounded and *corpus-specific*: eighteen pilot-2 drafts alone surfaced `must`, `shall`, `long`, `mean`, `principal`, `identify`, and **`shall` would have to be a stopword for NIST and a keyword for the AI Act simultaneously**. A hand-maintained per-document vocabulary is a maintenance burden that decays silently.
  - **(iii) `plainto_tsquery`.** Verified to also AND. Does nothing.
  - **(iv) change nothing, re-scope the claim to citation-shaped queries.** Not free: it keeps the second branch, the fusion step, the `tsvector` column and this spec's two-connection design while serving roughly half of queries, and `/query` accepts sentences.

  #### Pre-registered interpretation — written before the effect on retrieval is measured

  **(i) was chosen on correctness grounds and a negative effect on retrieval quality does not reverse it.** Recorded here, before the number exists, because written afterwards this is a rationalisation.

  - **If `recall@8` or the hybrid arm degrades once the branch starts working, that is evidence about RRF, not about this fix.** It would mean fusion handles a low-precision lexical candidate set badly — which is **SPEC-007 Key decision 12's question**, and one this repository already has a hint of: RRF measured as a *loss* on paraphrase queries on 2026-07-26, when the branch was firing on some of them.
  - **The fix is not conditional on the comparison improving.** A branch that returns nothing for structural reasons is broken whether or not fixing it flatters the metric. Reverting on a bad number would be selecting the implementation that makes hybrid win — SPEC-007 KD-12's substitution one layer down.
  - **What the re-measurement is for:** it tells us what RRF does with a working lexical branch. That is an input to SPEC-007, not a verdict on this amendment.

  #### Pre-specified failure mode, and the successor named now

  **How (i) fails: OR-of-terms on a sentence matches any chunk containing a common lexeme — `system`, `data`, `provider` — so the fallback can return a large, low-precision candidate set exactly where the AND form was silent.** Fusion then has to rank it, and RRF gives a candidate credit for its position in *either* list.

  **The falsifier, stated as an observable: fallback candidates displacing good vector results in the fused top-k.** Concretely — a question where hybrid's `recall@8` is worse than vector-only's *and* the displacing chunks carry a `fulltext_rank` originating from a fallback query. The `c` cell of the McNemar table (vector-only succeeds, hybrid fails) is where this shows up, and it was **0** across both pilots before the change.

  **The successor, named now so nobody reaches for (ii) later because it is the nearest thing to hand: prune the OR-set by corpus-derived chunk frequency — computed, never maintained.** A lexeme appearing in more than some fraction of chunks carries no retrieval signal and is dropped from the fallback query. The frequency comes from the indexed corpus itself (`ts_stat` over the `tsv` column, or a materialised count), so it **adapts to the corpus automatically and has no per-document list to maintain** — which is precisely the property (ii) lacks. It is not implemented now because the failure it addresses has not been observed.

  #### Measured after the change — 2026-08-02

  **Coverage: the defect is closed.** Questions on which the branch returns zero candidates:

  | Set | Before | After |
  |---|---:|---:|
  | smoke (26) | 12 (46 %) | **0** |
  | pilot-1, natural language (14) | 13 (93 %) | **0** |
  | pilot-2, lexically anchored (14) | 0 | **0** |

  **Nothing that worked stopped working.** Pilot-2 is byte-identical before and after on every measure — same `recall@8` (1.000 / 0.857), same discordance (b = 2, c = 0), same 6-of-14 identical top-8 — which is the direct check that the fallback only fires where the conjunction was silent. The count of questions where hybrid's top-8 equals vector-only's fell from 13/26 to 1/26 on the smoke set and 14/14 to 1/14 on pilot-1: **the second branch now differentiates.**

  **`recall@8` improved or held everywhere.** Pilot-1 hybrid 0.714 → **0.857** against an unchanged vector-only 0.714; smoke and pilot-2 unchanged.

  **And the pre-specified failure mode occurred.** `recall@1` on the smoke set:

  | | Before | After |
  |---|---:|---:|
  | hybrid overall | 0.769 | **0.500** |
  | hybrid citation | 0.929 | **0.714** |
  | hybrid paraphrase | 0.583 | **0.250** |
  | vector-only (all three) | 0.885 / 0.857 / 0.917 | unchanged |

  **Mechanism confirmed rather than assumed: of the 13 smoke questions that now miss at k = 1, 8 have a top-1 chunk that is full-text rank 1 and arrived through the fallback** (the conjunction returned zero for that query). RRF scores a fallback candidate at `1/(60+1)` for its lexical rank, which outranks a genuinely relevant chunk sitting at vector rank 3. This is the low-precision candidate set displacing good vector results, exactly as written down before the measurement.

  **The pre-specified falsifier was too narrow, and that is recorded rather than glossed.** It named only the `recall@8` manifestation — "hybrid's `recall@8` worse than vector-only's". That did **not** happen; `recall@8` improved. The failure appeared one rank-level up, as top-1 displacement. A falsifier that names one manifestation of a mechanism will miss the others, and this one did.

  **Consequence: AC-6 is now RED, and it stays red pending an owner decision.** AC-6 asserts hybrid `recall@1` on citation queries beats vector-only; measured 0.714 against 0.857. Under the pre-registered interpretation above **this does not reverse the change** — the correctness argument for (i) never depended on the comparison improving, and reverting on a bad number would be selecting the implementation that makes hybrid win. Three ways forward, **none applied**:

  1. **Demote AC-6's k = 1 assertion to *recorded*.** The strongest case on the merits and the weakest on process: Key decision 12 already says the k = 1 citation result on 26 questions is *"noise, not evidence"*, so AC-6 asserts a quantity this spec elsewhere disclaims — the same defect AC-8's withdrawn p50 had. But it is also, unavoidably, changing a test because it went red, and it should be reviewed as such.
  2. **Implement the named successor** (frequency pruning) and re-measure. Addresses the mechanism rather than the assertion, and the failure it targets has now been observed, which is the trigger it was waiting for.
  3. **Leave it red** until SPEC-007 Key decision 12 settles fusion with data. Honest, and it makes the suite uninformative in the meantime.

  **Recommendation: (2), then re-measure, and only then revisit AC-6.** The assertion is measuring something real — hybrid got worse at rank 1 — and demoting it first would remove the signal that the successor is supposed to move.

  #### Amendment 6 — corpus-derived frequency pruning *(2026-08-02, owner-approved: option (2))*

  **What it does.** Before the fallback query is built, each of the query's lexemes is counted against the indexed corpus, and any lexeme present in **more than 25 % of chunks** is dropped from the OR set. The count comes from the corpus itself at query time — **computed, never maintained**. There is no list, no per-document vocabulary, and nothing to update when a document is added; that is the property option (ii) lacked and the reason this is the successor rather than a stop-list by another name.

  **Why 25 %, chosen from the shape of the arithmetic rather than tuned against a metric.** A lexeme present in a quarter of chunks can at best partition the corpus 1 : 3, so it carries under one bit; inside an OR set it contributes more candidates than any discriminative term and therefore dominates what the branch returns. The value is **not tuned** — tuning it against `recall@1` would be selecting the implementation that makes hybrid win, and belongs to SPEC-007 Key decision 12 with data.

  **If pruning empties the set, the result is empty and that is correct, not a regression.** A query whose every lexeme is corpus-common has no lexical signal at all; returning the resulting garbage would be worse than returning nothing, and AC-12(b) already defines an empty full-text list as a valid outcome that degrades fusion to vector order.

  #### Expect it to help and not to close — the arithmetic, stated before the re-measurement

  **RRF reads rank and is blind to confidence.** With `RRF_K = 60`:

  | Candidate | Score |
  |---|---|
  | full-text rank 1, absent from the vector list | `1/61` = 0.016393 |
  | vector rank 1, absent from the full-text list | `1/61` = 0.016393 — **identical** |
  | vector rank 2 | `1/62` = 0.016129 |
  | vector rank 3 | `1/63` = 0.015873 |

  **A chunk leading a fallback list is worth exactly what the vector branch's best result is worth.** Solving `1/61 > 1/(60 + r)` gives `r > 1`: **a full-text rank-1 candidate outranks every vector candidate except vector rank 1, which it ties.** That is a property of the fusion rule, not of the fallback, and it holds whether the fallback's rank-1 chunk is excellent or worthless.

  **Therefore, pruning improves *what leads* the fallback list; it cannot change *what leading the list is worth*.** The predicted outcome is a partial recovery of `recall@1` — better chunks winning the 1/61 — with the structural displacement intact. **A partial close is the expected result and must not be read as pruning having failed.** Closing it fully requires making fusion sensitive to branch confidence — a weight, a threshold, or excluding fallback results from fusion entirely — and every one of those is a change to the fusion rule, which Key decision 12 reserves for SPEC-007 with data.

  #### Fallback provenance is recorded, and deliberately not used

  `CandidateRow` and `RetrievedChunk` carry whether the full-text list came from the conjunction or from the fallback. **Recording is not fusing**, so this respects Key decision 12's reservation, and it is the same argument that justified the fallback itself: the branch owes its caller *"empty because nothing matched"* versus *"empty because the query was unsatisfiable"*, and one level up it owes *"found by conjunction"* versus *"found by fallback"*.

  **Nothing weights on it, and a test asserts that fusion output is byte-identical with the flag set either way.** When SPEC-007 settles fusion with data, this field **is** the data — the question "should a fallback candidate be worth 1/61?" cannot be answered by a system that does not record which candidates were fallbacks.

  #### Measured after pruning — 2026-08-02

  Smoke set, 26 questions, hybrid arm. The middle column is the same code with `MAX_LEXEME_CHUNK_FRACTION` disabled, so the last two columns isolate pruning alone. **Vector-only is unchanged in every column** (`recall@1` 0.885, `@3` 1.000, `@8` 1.000).

  | Hybrid | Before the fallback | Fallback, no pruning | **Fallback + pruning** |
  |---|---:|---:|---:|
  | `recall@1` overall | 0.769 | 0.500 | **0.538** |
  | `recall@1` citation | 0.929 | 0.714 | **0.786** |
  | `recall@1` paraphrase | 0.583 | 0.250 | **0.250** |
  | `recall@3` overall | 1.000 | 0.962 | **0.923** |
  | `recall@8` overall | 1.000 | 1.000 | **0.962** |
  | `MRR@8` overall | 0.865 | 0.706 | **0.708** |
  | discordant (b / c) | 0 / 0 | 0 / 0 | **0 / 1** |

  **Pruning helped where it was predicted to help, by roughly the predicted amount: `recall@1` citation 0.714 → 0.786, overall 0.500 → 0.538.** It did not close the gap — vector-only remains at 0.857 and 0.885 — which is the arithmetic above behaving exactly as written down beforehand. A better chunk now wins the `1/61`; `1/61` is still what winning is worth.

  **And pruning made `recall@3` and `recall@8` worse, which was not predicted.** `recall@8` fell 1.000 → 0.962: one paraphrase question (`par-11`, patents and proprietary rights) lost its gold chunk from the top 8 entirely. Mechanism: pruning dropped `protect` (109 of 358 chunks, 30 %), which changed *which* 50 candidates the fallback returned and their order, and the gold fell out of a set the unpruned fallback had retained. **Pruning is not monotonically beneficial — it changes the candidate set, and a lexeme with little discriminating power can still be the one holding a particular gold chunk in the pool.**

  **This is CLAUDE.md rule 9 being violated one commit after it was written, and it is recorded as that rather than as a surprise.** The prediction above enumerated `recall@1` and nothing else. Rule 9 says: name the mechanism, then enumerate the metrics it could appear in, or declare the falsifier metric-independent. Pruning's mechanism is *changing the candidate set*, which can move any rank-sensitive metric in either direction, and the correct falsifier was metric-independent from the start.

  **Net position, stated without rounding in either direction.** Against the pre-fallback baseline the hybrid arm is worse at every k on the smoke set and better on the two pilot sets; against the no-pruning intermediate it is better at k = 1 and worse at k = 3 and k = 8. **The structural cause is untouched and was always going to be**: RRF cannot tell a confident branch from a desperate one. AC-6 stays `xfail(strict=True)` — the assertion still fails at 0.786 against 0.857 — and the trigger written into the marker is unchanged: it comes off when SPEC-007 Key decision 12 settles fusion with data, not when a threshold is nudged.



- **AC-13 (baseline artifacts are produced deliberately, never as a test side effect — Key decision 14)** *(added 2026-07-26, third review)* — asserted on **exit codes**, since the guard's entire job is to fail a run:
  - **Gated:** `uv run pytest` writes nothing under `evals/baselines/` and does not modify `evals/retrieval_baseline.json`. The quality tier's *assertions still run* — only the disk write is skipped, and the measured table is printed instead. `uv run pytest --write-baseline` performs the write.
  - **Guarded:** a run that changes any guarded artifact without `--write-baseline` **exits non-zero** with a message naming the file, the change (created / modified / deleted), and the flag. Verified by staging a throwaway artifact, never by overwriting a committed one.
  - **Immutability outranks the flag (SPEC-003 AC-13):** with `--write-baseline`, creating a new `evals/baselines/baseline-<N>-chunks.json` exits 0; **modifying or deleting an existing one exits non-zero** with a message naming SPEC-003 AC-13. `evals/retrieval_baseline.json` is the mutable most-recent-run copy and may be rewritten under the flag.
  - **Content-hashed, not mtime-based:** rewriting a guarded artifact with byte-identical content is not a violation. Asserted directly, so the guard cannot be "fixed" into timestamp comparison later.
  - **The guard's comparison is unit-tested against staged snapshots** — no corpus, no database, no API key — so it runs in CI, where the artifacts it protects are never written.

## Test plan

`tests/test_retrieval_fusion.py`, `test_retrieval_search.py`, `test_retrieval_service.py`, `test_retrieval_quality.py`, plus the migration test — async where DB-touching, reusing SPEC-002's binding fixture pattern (session-scoped engine, savepoint rollback), against the dockerized Postgres / CI service container.

Two tiers, mirroring SPEC-003:

- **Synthetic tier (CI, no network, no API key).** A seeding fixture inserts documents + ~40–200 chunks with **hand-constructed embedding vectors** (inserted directly, not via an embedding client) so dense-rank order is controlled exactly; texts are crafted so FTS winners are controlled independently. The query embedder is a stub returning a fixed vector with a settable `identity`. Backs AC-1–AC-4(a–d), AC-7, AC-8(CI), AC-9, AC-10, AC-11, AC-12. AC-4(e) reuses SPEC-003's synthetic ingest fixtures end-to-end with the fake embedder. Fusion tests (AC-2) are pure — no DB.
- **Real-corpus tier (local, `skipif` like SPEC-003).** `test_retrieval_quality.py` requires the ingested corpus (`rag` database) and `OPENAI_API_KEY` for query embeddings — skipped in CI. Runs AC-6 and AC-8(local); prints the per-query table (question, hybrid rank, vector-only rank, expected section) and — **only under `--write-baseline` (AC-13)** — writes `evals/retrieval_baseline.json` (recall@{1,3,8} per subset, MRR@8, diversity distribution, stage-latency split). The distinct-section-rate distribution is the first real measurement SPEC-003 Key decision 12 asked for: **measured mean 0.832, median 0.875, min 0.375** at k=8 — clustering exists but is mild, so no de-duplication/MMR work is justified yet.

Migration test (AC-5) follows SPEC-002's scratch-database pattern: seed unqualified rows and a NIST-URI document at revision 0002, upgrade, assert the embedding_model rewrite and the `doc_type` backfill, downgrade, assert restore.

Concurrency test (AC-7) monkeypatches `search.vector_search` / `search.fulltext_search` with delayed wrappers recording `(start, end, pg_backend_pid)`; overlap is asserted from timestamps, not timing luck (0.55 s bound on two 0.3 s sleeps fails only if execution is sequential).

Baseline guard (AC-13) is a third, degenerate tier: `tests/baseline_guard.py` holds the snapshot/compare logic as pure functions and `conftest.py` wires them into `pytest_sessionstart` / `pytest_sessionfinish`. `tests/test_baseline_guard.py` exercises the comparison against staged snapshots in `tmp_path` rather than by damaging a real artifact — the guard has to be trustworthy in CI, where the files it protects are never written and a real violation can never be staged.

Tests are written from these ACs and committed before/with the implementation, in the same commit series referencing SPEC-004.
