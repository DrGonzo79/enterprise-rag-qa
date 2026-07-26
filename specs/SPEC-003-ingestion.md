# SPEC-003 — Corpus Ingestion Pipeline

**Status:** Approved — 2026-07-25 (with review amendments: dropped-table manifest, EDGAR-scoped table rule, hash covers all chunk-affecting parameters)
**Date:** 2026-07-25
**Depends on:** SPEC-002

## Purpose

Turn the three corpus documents into embedded, retrievable chunks in the SPEC-002 schema: format-specific loaders → shared normalization → heading-aware chunking → batched embedding → idempotent upsert keyed on `documents.content_hash`. One CLI (`python -m rag_qa.ingest`), async internally behind a sync `asyncio.run` entrypoint (SPEC-002 Key decision 5).

### What the source documents actually look like (measured 2026-07-25, files in `corpus/`)

| | NIST AI RMF (PDF) | EU AI Act (EUR-Lex HTML) | NVDA 10-K (EDGAR iXBRL) |
|---|---|---|---|
| Size | 48 pages, ~106K text chars | 1.3 MB, ~602K text chars | 2.0 MB, ~357K text chars |
| Structure signal | PDF outline (bookmarks) with the full section hierarchy, 4 levels deep | Semantic: `eli-subdivision` divs with ids `rct_1`–`rct_180` (recitals), `art_1`–`art_113` (articles); `oj-ti-art`/`oj-sti-art` article titles; `oj-ti-section-1/2` chapters & sections; **13 annexes marked only by `oj-doc-ti` headings, no eli ids** | **Zero `h1`–`h6`.** Headings are styled `<span style="font-weight:700">` (541 of them); "Item N." appears **twice** each (TOC link + body heading) |
| Hazards | Running header `NIST AI 100-1 AI RMF 1.0` on 43/48 pages; line-break hyphenation ("bene-\nfits"); front matter (TOC, lists of figures); Part 2 category tables extract as linear text (acceptably) | 851 `<table>` elements that are **layout tables** for numbered points (a)/(b) — not data; footnotes (`oj-note`); `art_3` (definitions) is 18K chars, far over any chunk budget | 1,402 inline-XBRL tags interleaved with prose; hidden `ix:header` with 19K chars of metadata; **Windows-1252 encoding** (0x92 smart quotes; `file` misreports ASCII); 64 tables holding ~8% of text — financial statements are dense tables, narrative dominates everywhere else |
| Fetch | Plain HTTPS, no auth | **AWS WAF JavaScript challenge** — plain `curl`/httpx gets HTTP 202 + challenge page, 0 bytes. Verified today; a real browser passes | Requires descriptive `User-Agent` with contact info (already noted in `corpus/README.md`) |

**Difficulty ranking: 10-K ≫ NIST PDF > EU AI Act.** The EU Act is the *easiest* despite its size — EUR-Lex ships stable semantic ids per article/recital. The PDF is middling: bookmarks give the hierarchy; the work is text cleanup. The 10-K is hardest: heading detection is style-sniffing that is issuer-specific, XBRL noise is interleaved with sentences, and its tables are the only *data* tables in the corpus.

**Scope recommendation (decision 10, flagged per review request):** keep all three documents, but (a) pin the EDGAR loader to this one filing rather than "any 10-K", and (b) drop predominantly-numeric tables from chunking entirely in v1 — no table parser. If forced to cut a document later, cut the 10-K first; never the other two.

## Non-goals

- Retrieval, RRF fusion, ranking (later spec)
- A general-purpose EDGAR parser — the loader targets `nvda-10k-2026.htm` specifically; a second filing is a spec amendment
- Financial-statement table extraction or table QA — numeric tables are excluded from chunks (decision 10); revisiting this is its own spec
- OCR / scanned-PDF handling — the NIST PDF has a text layer
- Incremental / partial re-ingestion — the unit of idempotency is the whole document (matches `content_hash` semantics)
- Re-embedding existing chunks under a new embedding model (detectable via `chunks.embedding_model`, out of scope here)

## Interface

### Modules

```
scripts/fetch_corpus.py            # download the three sources into corpus/
src/rag_qa/ingest/
    __main__.py                    # python -m rag_qa.ingest → cli()
    types.py                       # Section, ChunkDraft dataclasses; IngestConfig
    loaders/
        nist_pdf.py                # pypdf; outline-driven sectioning
        eurlex_html.py             # BeautifulSoup + lxml; eli-subdivision driven
        edgar_10k.py               # BeautifulSoup + lxml; Item-regex + bold-span driven
    normalize.py                   # shared text cleanup
    chunker.py                     # heading-aware packing, sentence-safe
    embedder.py                    # AsyncOpenAI, batched, bounded concurrency
    pipeline.py                    # hash → skip/replace decision → upsert
alembic/versions/0002_*.py         # adds chunks.section_path (decision 4)
```

### Loader contract

Every loader implements `load(path: Path) -> ParsedDocument` where:

```python
@dataclass(frozen=True)
class DroppedTable:
    document: str  # source filename
    item: str  # nearest heading (e.g. "Item 8.")
    table_index: int  # document-order index among the document's tables
    digit_ratio: float
    cell_count: int
    reason: str  # "numeric_table_threshold"


@dataclass(frozen=True)
class Section:
    heading_path: tuple[str, ...]  # ("Part 2: Core and Profiles", "AI RMF Core", "Govern")
    text: str  # normalized body text, no heading


@dataclass(frozen=True)
class ParsedDocument:
    source_uri: str  # canonical URL from corpus/README.md
    title: str
    raw_bytes: bytes  # exact file bytes — the content_hash input
    sections: list[Section]  # document order
    dropped_tables: list[DroppedTable]  # empty for all loaders except edgar_10k
```

Per-format sectioning:

- **nist_pdf** — sections come from the PDF outline (16 top-level entries, verified). Text between consecutive bookmark targets belongs to the earlier bookmark. Front matter before "Executive Summary" (title page, TOC, lists of figures/tables) is discarded.
- **eurlex_html** — one section per `eli-subdivision` with an `rct_*` or `art_*` id; `heading_path` is (chapter, section, article-title) from the enclosing `oj-ti-section-*` and the article's `oj-ti-art` + `oj-sti-art`. Recitals get `("Preamble", "Recital (N)")`. Annexes are segmented by consecutive `oj-doc-ti` pairs ("ANNEX N" + its title) since they carry no eli ids. Citations (`cit_*`) and the final-provisions wrapper are discarded. Layout tables are linearized cell-by-cell in document order so point lists read as prose: `(a) …`, `(b) …`.
- **edgar_10k** — decode with UTF-8 → **fallback cp1252** (verified necessary). Strip `ix:header` entirely; unwrap (keep text of) all other `ix:*` elements. Item boundaries via regex `^Item\s+\d+[A-C]?\.` on block text, **taking the last occurrence of each** (the first is the TOC; last-wins also resolves nested blocks wrapping the same heading); sub-headings within an Item are blocks whose entire text is one `font-weight:700` span of 5–90 chars. Tables: compute a numeric ratio (digit-cell fraction); tables ≥ 50% numeric cells are dropped, others linearized row-wise with a `|` separator (decision 10). **The drop rule is scoped to this loader only** — the EU AI Act's 851 layout tables carry point lists dense with citations ("No 300/2008", "Article 6(2)") that could cross a digit threshold; `eurlex_html` linearizes every table unconditionally (decision 11).

### Normalization (`normalize.py`, applied by every loader)

1. Unicode NFKC; collapse runs of whitespace; normalize `\xa0` to space (EUR-Lex uses NBSP heavily — verified in headings).
2. De-hyphenation of line-break hyphens: `(\w)-\n(\w)` → join (PDF only; verified "bene-\nfits", "di-\nmensions").
3. Strip repeated page furniture: any line equal to the modal first/last page line (catches `NIST AI 100-1 AI RMF 1.0`, bare page numbers).
4. Footnote markers kept inline. Footnote bodies: EUR-Lex note bodies live in the end-of-document footnote panel (`fnp_1`) and are OJ citation references — dropped with the panel; PDF footnote text remains inline at its extraction point (separating it is layout analysis, out of proportion here). *(Amended during implementation from "append bodies as `[fn N]`" — mapping panel notes back to their sections requires anchor resolution that buys nothing for citation-reference footnotes.)*

### Chunking (`chunker.py`)

`IngestConfig(strategy="heading_v1", target_min=500, target_max=800, overlap_ratio=0.15, hard_min=120, edgar_numeric_table_threshold=0.5, breadcrumb_format="v1:›")` — tokens counted with tiktoken `cl100k_base` (SPEC-002 decision 4). This one frozen dataclass is the sole source of chunk-affecting parameters and is what the content hash serializes.

- Packing unit: a **pysbd sentence within a line** (rule-based segmentation survives "Art. 6(2)", "No. 300/2008" better than regex). Lines are hard boundaries because loaders emit one line per layout-table point row — legal semicolon chains would otherwise register as one giant "sentence".
- Chunks never cross a `heading_path` boundary at the *top packing level*: sections are packed greedily with whole sentences; a section over budget is split at sentence boundaries (`art_3` at 18K chars ⇒ multiple chunks). Consecutive sections sharing the same parent heading whose sum is < `target_min` are packed together (recitals pack several per chunk; a recital is atomic — never split across chunks unless it alone exceeds the budget).
- The breadcrumb counts against the token budget: packing targets `target_max` minus the group's longest path plus one extra leaf label (range labels repeat a leaf: "Article 88 … – Article 91 …"), so `token_count ≤ target_max` holds after prefixing.
- Overlap: each continuation chunk **within the same section** starts with the trailing whole sentences of its predecessor totaling ≥ `overlap_ratio × target_max` tokens (first sentence crossing the threshold completes the overlap). No overlap across heading boundaries.
- No mid-sentence splits, with one escape hatch: a single sentence over the unit budget is hard-split at a token boundary and logged as a warning.
- **Undersized-chunk rescue (added during implementation):** a packed chunk under `hard_min` — an isolated section with no same-parent sibling to pack with, e.g. "Item 1B. Unresolved Staff Comments — None." — merges into an adjacent chunk when the combined chunk stays ≤ `target_max`; its breadcrumb becomes the common-ancestor range ("Item 1A … – Item 1B …"). A tail fragment of a split section instead steals trailing sentences from its predecessor. Chunks that still cannot reach `hard_min` (no neighbor with room) are kept and logged — measured: ≤ 2 per document on the real corpus.
- Each chunk's stored `text` is prefixed with its breadcrumb line (`NIST AI RMF 1.0 › AI RMF Core › Govern`) — heading terms of art then hit both the tsvector and the embedding. The breadcrumb also lands in `chunks.section_path` (decision 4).

### Embedding (`embedder.py`)

- `AsyncOpenAI().embeddings.create`, model `text-embedding-3-small`, batches of ≤ 128 inputs.
- Bounded concurrency: `asyncio.Semaphore(4)` across batch requests.
- Retry 429/5xx/connection errors with exponential backoff (3 attempts). *(Amended during implementation: hand-rolled in `embed_all` rather than `tenacity` — the retry loop is ~10 lines, lives where tests can exercise it with a fake client, and drops a dependency.)*
- `chunks.embedding_model` set to the literal model name on every row.

### Pipeline & CLI

`content_hash = sha256(raw_bytes + b"\x00" + canonical_config)` where `canonical_config = json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()` — SPEC-002 decision 9's "content ‖ chunking config". The serialized config is the **complete** `IngestConfig`, covering every parameter that can change chunk output: strategy name, `target_min`/`target_max`, `overlap_ratio`, `hard_min`, the EDGAR numeric-table drop threshold, and the breadcrumb separator/format version. Adding a chunk-affecting parameter without routing it through `IngestConfig` violates Key decision 9 and is a review-blocking defect.

**Ingestion manifest:** every run writes `corpus/ingest-manifest.json` (git-ignored) and logs the same records — one entry per **dropped table**: `{document, item, table_index, digit_ratio, cell_count, reason}` plus per-document chunk/token totals and new/unchanged/replace verdicts (verdict literal for a hash match: `unchanged`). **Cross-spec note (binding on SPEC-007):** golden-set authoring must consult this manifest — questions answerable only from dropped tables are classified as out-of-scope refusals (refusal is a scored capability per the charter), never as answer failures.

Per file: hash → if `documents.content_hash` exists, **skip** (no parse, no API calls) → else parse, normalize, chunk, embed (outside any DB transaction) → **one transaction**: delete any existing document with the same `source_uri` (cascade drops its chunks), insert document + chunks with sequential `ordinal`.

```
uv run python -m rag_qa.ingest corpus/            # ingest all recognized files
uv run python -m rag_qa.ingest corpus/ --dry-run  # parse+chunk only: per-file section/chunk/token counts,
                                                  # est. embedding cost, verdict; no DB, no API
uv run python -m rag_qa.ingest corpus/ --embedder fake   # offline: deterministic local pseudo-vectors
```

`--embedder fake` exists for offline smoke runs and the cross-process idempotency test: deterministic sha256-seeded vectors, no network; when `RAG_QA_FAKE_EMBEDDER_LOG` names a file, each embed call appends a line so tests can count calls across process boundaries.

Entrypoint is a plain sync function calling `asyncio.run(main())` (SPEC-002 decision 5). File-type routing by suffix + sniff: `.pdf` → nist_pdf, EUR-Lex classes present → eurlex_html, `ix:` namespace present → edgar_10k.

### `scripts/fetch_corpus.py`

- NIST: direct GET from nvlpubs.nist.gov.
- EDGAR: GET with `User-Agent: enterprise-rag-qa (thompsn79@gmail.com)` — rejected without it (corpus/README.md).
- EUR-Lex: try direct GET; on the verified WAF response (HTTP 202 / challenge body), fall back to the Cellar content-negotiation endpoint (`publications.europa.eu/resource/celex/32024R1689`, `Accept: text/html`, `Accept-Language: en`); if that also fails, print the manual-download instruction and exit non-zero. **Open risk, verified today:** plain-HTTP fetch of EUR-Lex is not guaranteed; the committed corpus workflow must tolerate a manually downloaded file.
- Each fetch validates minimum size (> 100 KB) so a challenge page can never silently pass as corpus (today's failure mode: a 0-byte `eu-ai-act-2024-1689.html`).

### New dependencies

`pypdf`, `beautifulsoup4`, `lxml`, `pysbd`, `openai` (embeddings only; generation adapter is a later spec). All runtime deps of the ingestion path. *(`tenacity` was planned and dropped — see Embedding.)*

## Key decisions

1. **Loaders emit a shared `Section` intermediate; chunker is format-blind.** All format knowledge is quarantined in three loader modules; chunking/embedding/upsert are written once. Adding a corpus document means one loader, nothing else.
2. **Structure comes from each format's native signal, not a generic heuristic**: PDF outline for NIST, `eli-subdivision`/`oj-*` classes for EUR-Lex, Item-regex + bold-span for EDGAR. A single "universal" heading detector was rejected — the measured documents share no common signal (bookmarks vs. semantic ids vs. styled spans).
3. **pysbd for sentence boundaries** — the no-mid-sentence-split rule is only as good as the segmenter, and this corpus is adversarial for naive splitters ("Art. 6(2)", "Regulations (EC) No 300/2008", "Item 7A."). Rejected: regex splitting (fails above), spaCy (heavyweight dependency for one function).
4. **Amend `chunks` with `section_path text not null` (migration `0002`)** — retrieval answers must cite *where* ("Article 6(2)", "Item 1A"); without a structured column, citation display would re-parse the breadcrumb out of chunk text. Schema-touching, so this spec owns migration 0002 per SPEC-002's one-migration-per-spec rule. SPEC-002's chunk table is otherwise unchanged.
5. **Breadcrumb prefixed into chunk `text`** (in addition to the column) — deliberately influences both `tsv` and the embedding: a chunk from "Item 1A. Risk Factors" should match a query saying "risk factors" even when the body text doesn't repeat the phrase.
6. **Whole-document replace, no chunk-level diffing** — on config or content change, delete-cascade + reinsert in one transaction. Chunk-level reconciliation buys nothing at 3 documents and complicates `ordinal` integrity.
7. **Embedding before the DB transaction** — API calls are the slow, fallible part; holding a transaction across them risks pool exhaustion (SPEC-002 decision 8 bounds the pool at 10). Crash between embed and commit costs one document's re-embedding (~cents), not consistency.
8. **Numeric-table exclusion threshold at 50% digit-cells** (part of decision 10) — measured: 10-K narrative tables (e.g., securities listings) mix labels and numbers; financial statements are near-all-numeric. The threshold separates them without a table parser. Excluded tables are logged with their Item so the eval set can avoid asking about them.
9. **Encoding: try UTF-8, fall back cp1252, never `errors="replace"`** — the 10-K decodes cleanly as cp1252 (verified); silent replacement would corrupt exactly the smart-quote characters that appear mid-sentence. A decode failure under both is a hard error.
10. **Scope: keep all three documents; pin EDGAR loader to this filing; no table parsing in v1.** Dropped tables are not silent: each is recorded in the ingestion manifest (see Pipeline) so downstream specs can treat the content as knowingly out of corpus. Ranking and evidence in Purpose. The 10-K stays because the corpus was chosen for structural variety (corpus/README.md) and its *narrative* (Items 1, 1A, 7) parses fine — it is the tables that are expensive, so the tables are what gets cut, not the document. Deferred, each requiring a future spec: generic multi-issuer EDGAR support; financial-table QA. If a document must be cut under schedule pressure: 10-K first, per the scope-cut ladder's spirit; NIST and EU Act are never cut (they anchor the compliance-QA premise).
11. **Numeric-table drop rule is EDGAR-only** (review amendment). Applying it corpus-wide risked silently deleting EU AI Act point lists whose citation density ("No 300/2008", "Article 6(2)") can push digit ratios over the threshold. `eurlex_html` and `nist_pdf` never drop content on numeric grounds; AC-3 asserts zero EU AI Act tables dropped.
12. **Known retrieval side-effect to measure, not solve here** (review note): prefixing breadcrumbs into chunk text raises intra-section similarity, so top-k retrieval may return several chunks from the same article/Item. The eval harness (SPEC-007+) should measure result diversity (e.g., distinct-section rate in top-k) before any de-duplication or MMR-style fix is considered. Report observed clustering after the first real ingest.

## Acceptance criteria

- **AC-1 (fetch)** — `uv run python -m scripts.fetch_corpus` into an empty `corpus/` leaves three files each > 100 KB, or exits non-zero naming the failed source with manual instructions. Unit-testable via mocked transport: the EDGAR request carries a `User-Agent` containing an email; a 202-challenge EUR-Lex response triggers the Cellar fallback URL.
- **AC-2 (loader fidelity, against real corpus)** — `eurlex_html` yields exactly 113 article sections, 180 recital sections, 13 annex sections; `nist_pdf` yields sections whose heading set includes every **content-bearing** top-level outline title and none titled "Contents"/"List of Figures" *(amended: "Part 1"/"Part 2" are container headings with no body text of their own and sit at depth 0 in the PDF's flat outline, so they yield no section)*; `edgar_10k` yields each of Items 1–15 (with letter variants) **exactly once**.
- **AC-3 (normalization & table scoping)** — no chunk text contains `NIST AI 100-1`, any `ix:header`-derived string, or U+FFFD; the de-hyphenation unit test maps `bene-\nfits` → `benefits`; no chunk from an excluded numeric table exists (spot-check: no chunk whose text is > 50% digits); **`eurlex_html` reports zero dropped tables on the real EU AI Act file and on a synthetic citation-dense layout table whose digit ratio exceeds the EDGAR threshold** (decision 11).
- **AC-4 (chunking invariants, all documents)** — every chunk `token_count ≤ 800`; every chunk ≥ 120 tokens except rare isolated sections no neighbor can absorb (measured bound: ≤ 2% of a document's chunks); median chunk in [500, 800]; every chunk boundary is a sentence-unit boundary (re-segment and assert alignment); consecutive chunks within a section share an overlap of whole sentences totaling ≥ 15% of `target_max` tokens; no overlap across `heading_path` changes.
- **AC-5 (batching & concurrency)** — with an instrumented fake embedding client: total embedded inputs equals chunk count, every request ≤ 128 inputs, max in-flight requests ≤ 4, a simulated 429 is retried and succeeds.
- **AC-6 (idempotency, SPEC-002 decision 9)** — second run, same config: zero new rows in `documents`/`chunks` and **zero** embedding-client calls. Run with `overlap_ratio` changed: `content_hash` differs, document row count unchanged, all chunk ids for that document replaced. **Changing `edgar_numeric_table_threshold` or `breadcrumb_format` alone also changes `content_hash`** (review amendment 3 — every chunk-affecting parameter invalidates the hash). **Cross-process form (added after the shared-DB incident):** running the CLI twice in separate processes against the same real database yields second-run verdicts `unchanged` for every document, zero additional embedding calls, and identical row counts — in-transaction fixtures cannot verify this (SPEC-002 test-plan limitation), so it is tested by subprocess in `tests/test_ingest_idempotency.py`. `content_hash` determinism across processes is a precondition and was verified explicitly (identical canonical JSON and digests from separate interpreters).
- **AC-7 (migration)** — `alembic upgrade head` from 0001 adds `chunks.section_path`; `downgrade -1` removes it; both exit 0.
- **AC-8 (entrypoint & dry-run)** — `uv run python -m rag_qa.ingest corpus/ --dry-run` exits 0 with no `DATABASE_URL` set and no API key set, printing per-file chunk/token counts and estimated embedding cost; the module entrypoint is sync and its body is a single `asyncio.run(...)` call (SPEC-002 decision 5).
- **AC-9 (manifest)** — after a run (including `--dry-run`), `corpus/ingest-manifest.json` exists and contains one record per dropped table with `document`, `item`, `table_index`, `digit_ratio`, `cell_count`, `reason`, plus per-document verdicts; on the synthetic EDGAR fixture the numeric table appears in the manifest and the narrative table does not. `corpus/ingest-manifest.json` is git-ignored.

## Test plan

`tests/test_ingest_loaders.py`, `test_ingest_chunker.py`, `test_ingest_pipeline.py`, `test_fetch_corpus.py` — async where DB-touching, reusing SPEC-002's binding fixture pattern (session-scoped engine, savepoint rollback).

Two fixture tiers, so CI never needs the network:

- **Synthetic fixtures** (committed, small, hand-built to reproduce measured structure): a EUR-Lex-style HTML snippet with `eli-subdivision`/`oj-ti-art`/layout-table point lists; an EDGAR-style snippet with duplicate Item headings, `ix:` tags, a cp1252 byte (0x92), one numeric and one narrative table; a 3-page PDF generated in-fixture with pypdf carrying an outline and a repeated header line. These back AC-1, AC-3, AC-4 (invariant logic), AC-5, AC-6, AC-8 in CI.
- **Real-corpus tests** (`@pytest.mark.skipif(not CORPUS_PRESENT, ...)`): AC-2's exact counts and AC-4's invariants over the full documents run locally against `corpus/`; CI skips them. Rationale: EUR-Lex WAF makes networked CI fetch non-deterministic.

Embedding client is faked everywhere (deterministic 1536-dim vectors, call recorder for AC-5/AC-6); no API key in CI. Pipeline tests run against the dockerized Postgres (chunk insert exercises the real vector/tsv columns). Migration test (AC-7) follows SPEC-002's scratch-database pattern.

Test order follows the workflow rule: these tests are written from the ACs above and committed before/with the implementation, in the same commit series referencing SPEC-003.
