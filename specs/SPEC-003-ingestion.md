# SPEC-003 — Corpus Ingestion Pipeline

**Status:** Approved — 2026-07-25 (with review amendments: dropped-table manifest, EDGAR-scoped table rule, hash covers all chunk-affecting parameters).
**Amendment — 2026-07-26, corpus expansion: DRAFT, awaiting architect review.** Expands the corpus **until recall@8 de-saturates, measured one rung at a time — no chunk-count target** (SPEC-004 Key decision 12a). Rung 1 is seven confusability-chosen documents (~646 chunks); Rungs 2–3 are prepared but unauthorized and fetched only on evidence. Existing loaders only; EDGAR stays pinned. **Nothing is fetched until this amendment is approved and Rung 1's probe results are reviewed.** Sections carrying amendment content are marked *(amendment 2026-07-26)*.
**Amendment — 2026-08-02, embedding spend ledger: APPLIED (owner-asked).** New **AC-14**: the test suite prices and bounds its own real embedding calls. Independent of the corpus-expansion amendment above and does not depend on it.
**Date:** 2026-07-25
**Depends on:** SPEC-002

## Purpose

Turn the corpus documents — three at first approval, expanding by measured rungs under the 2026-07-26 amendment — into embedded, retrievable chunks in the SPEC-002 schema: format-specific loaders → shared normalization → heading-aware chunking → batched embedding → idempotent upsert keyed on `documents.content_hash`. One CLI (`python -m rag_qa.ingest`), async internally behind a sync `asyncio.run` entrypoint (SPEC-002 Key decision 5).

### What the source documents actually look like (measured 2026-07-25, files in `corpus/`)

| | NIST AI RMF (PDF) | EU AI Act (EUR-Lex HTML) | NVDA 10-K (EDGAR iXBRL) |
|---|---|---|---|
| Size | 48 pages, ~106K text chars | 1.3 MB, ~602K text chars | 2.0 MB, ~357K text chars |
| Structure signal | PDF outline (bookmarks) with the full section hierarchy, 4 levels deep | Semantic: `eli-subdivision` divs with ids `rct_1`–`rct_180` (recitals), `art_1`–`art_113` (articles); `oj-ti-art`/`oj-sti-art` article titles; `oj-ti-section-1/2` chapters & sections; **13 annexes marked only by `oj-doc-ti` headings, no eli ids** | **Zero `h1`–`h6`.** Headings are styled `<span style="font-weight:700">` (541 of them); "Item N." appears **twice** each (TOC link + body heading) |
| Hazards | Running header `NIST AI 100-1 AI RMF 1.0` on 43/48 pages; line-break hyphenation ("bene-\nfits"); front matter (TOC, lists of figures); Part 2 category tables extract as linear text (acceptably) | 851 `<table>` elements that are **layout tables** for numbered points (a)/(b) — not data; footnotes (`oj-note`); `art_3` (definitions) is 18K chars, far over any chunk budget | 1,402 inline-XBRL tags interleaved with prose; hidden `ix:header` with 19K chars of metadata; **Windows-1252 encoding** (0x92 smart quotes; `file` misreports ASCII); 64 tables holding ~8% of text — financial statements are dense tables, narrative dominates everywhere else |
| Fetch | Plain HTTPS, no auth | **AWS WAF JavaScript challenge** — plain `curl`/httpx gets HTTP 202 + challenge page, 0 bytes. Verified today; a real browser passes | Requires descriptive `User-Agent` with contact info (already noted in `corpus/README.md`) |

*(Amendment 2026-07-26: the table above describes the original three documents. The corpus expands one measured rung at a time until recall@8 de-saturates — see **Corpus expansion** under Interface — because 358 chunks cannot support a falsifiable retrieval metric. No target size is set; Rung 1 adds seven documents and may be the only rung. The expansion adds no formats and no filings: EUR-Lex HTML and bookmarked NIST PDFs only.)*

**Difficulty ranking: 10-K ≫ NIST PDF > EU AI Act.** The EU Act is the *easiest* despite its size — EUR-Lex ships stable semantic ids per article/recital. The PDF is middling: bookmarks give the hierarchy; the work is text cleanup. The 10-K is hardest: heading detection is style-sniffing that is issuer-specific, XBRL noise is interleaved with sentences, and its tables are the only *data* tables in the corpus.

**Scope recommendation (decision 10, flagged per review request):** keep all three documents, but (a) pin the EDGAR loader to this one filing rather than "any 10-K", and (b) drop predominantly-numeric tables from chunking entirely in v1 — no table parser. If forced to cut a document later, cut the 10-K first; never the other two.

## Non-goals

- Retrieval, RRF fusion, ranking (later spec)
- A general-purpose EDGAR parser — the loader targets `nvda-10k-2026.htm` specifically; a second filing is a spec amendment. **Reaffirmed by the 2026-07-26 amendment:** the corpus expansion adds *no* filings and does not touch `edgar_10k.py`
- **New parsing logic of any kind (amendment 2026-07-26)** — the expansion is restricted to formats the existing `eurlex_html` and `nist_pdf` loaders already handle. A candidate document that does not parse under an existing loader is dropped from the set, never accommodated with new heuristics
- **Non-English corpora, and EU acts in non-OJ-HTML renderings (amendment 2026-07-26)** — PDF renderings of EUR-Lex acts are out of scope even though `nist_pdf` exists; that loader is outline-driven and EUR-Lex PDFs are not bookmarked the same way
- Financial-statement table extraction or table QA — numeric tables are excluded from chunks (decision 10); revisiting this is its own spec
- OCR / scanned-PDF handling — the NIST PDF has a text layer
- Incremental / partial re-ingestion — the unit of idempotency is the whole document (matches `content_hash` semantics)
- Re-embedding existing chunks under a new embedding model (detectable via `chunks.embedding_model`, out of scope here)

## Interface

### Modules

```
scripts/fetch_corpus.py            # download corpus sources; resumable (amendment 2026-07-26)
corpus/corpus.toml                 # per-document metadata registry (amendment 2026-07-26)
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

### Corpus expansion *(amendment 2026-07-26)*

**Why:** at 358 chunks, top-8 is 2.2% of the corpus and every relevant chunk reaches it by either retrieval method — recall@3 and recall@8 are pinned at 1.000, and the k=1 comparison decides only 7 of 26 questions (SPEC-004 KD-12/12a). Retrieval metrics are therefore unfalsifiable, and SPEC-007's improvement loop would have no headroom. This is arithmetic about corpus size, not a defect in fusion, chunking, or embedding.

**Target: expand until recall@8 de-saturates, measured. There is no chunk-count target** *(restated after review, 2026-07-26)*.

An earlier draft of this amendment set a target of ~3,300 chunks with a floor of 3,000, and then selected Tier 3 to reach it. That is the same defect this project removed from SPEC-004 AC-6: **a threshold guessed before measurement, with work chosen to satisfy the guess.** It is worse here, because the draft's own reasoning says composition rather than volume does the work — competitor density rising from ~0–3 to 30–60 per query comes from the confusability-chosen Tier 1 documents, not from bulk. A number derived from bulk therefore contradicted the mechanism it was supposed to serve.

Restated: **the tiers are a prepared ladder, not a commitment.** Each rung is fetched, ingested, and measured before the next is considered; the ladder stops as soon as the measurement says the metric works. Chunk counts appear below only as *fetch-planning estimates* — what a rung will cost to fetch and ingest — never as goals.

**Falsifiable hypothesis, which Tier 1 tests directly:** competitor density, not corpus size, drives de-saturation. If true, **Tier 1 alone (~646 new chunks, ~1,004 total, 2.8× current) de-saturates recall@8 and Tiers 2–3 are never fetched.** If Tier 1 leaves recall@8 pinned at 1.000, the hypothesis is wrong or incomplete and the ladder continues — which is itself a finding worth recording, because it would mean raw scale matters more than this spec assumes.

**Stop condition (AC-10) — `recall@8 < 1.000` PERMITS stopping, it does not MANDATE it** *(clarified, second review)*. The literal condition is **necessary but not sufficient**: recall@8 = 0.99 on a 26-question set is a single miss and no usable headroom, and a rule that mandated stopping there would have swapped one guessed threshold for a technicality. The asymmetry is deliberate:

- **recall@8 = 1.000 exactly → escalation is mandatory.** Zero headroom is not a judgment call; the metric cannot move up.
- **recall@8 < 1.000 → stopping is permitted**, and requires a recorded judgment that the headroom is usable for the improvement loop. Escalating instead is equally permitted and requires the same recording. **Both directions are decisions made with the distribution in hand, and both are recorded with the numbers they rest on.**

No minimum recall value, chunk count, or document count is specified anywhere in this amendment, and none may be added retroactively to justify a rung already fetched. The discretion this leaves is bounded not by a number but by two rules: the judgment must be recorded with its evidence, and it must be argued in terms of the tuning metric below — never in terms of decided-question counts.

**Headroom must exist in the metric SPEC-007 tunes against, not merely in recall@8** *(clarified, second review)*. SPEC-004 declares that metric — **primary `recall@8`, diagnostic `MRR@8`** — and the reasoning for binding the headroom requirement to the primary is there. The practical consequence for this ladder: **MRR@8 headroom while recall@8 is saturated does not make the corpus adequate**, because rank-shuffling inside an already-complete top-8 changes context efficiency, not what evidence the generator can reach. If SPEC-007 adopts a different primary metric, it re-runs this de-saturation check against that metric.

**Saturation and statistical power are different problems, and the corpus is the wrong lever for the second.**

- **Saturation is a corpus property.** recall@8 pinned at 1.000 means too few plausible competitors. More corpus fixes it. This is what the ladder addresses.
- **Statistical power is an eval-set property.** Only 7 of 26 questions were *decided* (exactly one method ranking the target first). A two-sided sign test needs **≥ 6 decided questions** to reach p < 0.05 even when the result is unanimous (2 × 0.5⁶ = 0.031; five pairs give 0.063) — and more than that when the split is not unanimous, which is the realistic case.
- **Correction (second review, 2026-07-26).** An earlier draft justified keeping decided-counts out of the stop condition by asserting that more corpus does *not* raise the decision rate. **That claim was probably false and is withdrawn:** a harder corpus makes the two methods disagree more often, so the decision rate very likely *rises* with corpus size — which is why the observed rate must be re-measured at the final corpus state rather than assumed (SPEC-004's retrieval-only eval-set note).
- **The bar stands, on the correct grounds: corpus is sized for realism and headroom; question sets are sized for power.** Growing the corpus to make a secondary question answerable **optimizes the wrong artifact** — it changes the system under test in order to make a measurement *about* that system easier, and what you end up with is the corpus that suited the experiment rather than the one that reflects the retrieval problem the product actually faces. That the lever might work is not a reason to pull it. **If decided-counts are short, add questions, not documents.**

**Fetch-planning estimates only.** Cost per rung, at ~570 tokens/chunk and $0.02/1 M (`EMBEDDING_USD_PER_MTOK`): **Tier 1 ≈ 646 chunks ≈ $0.007**; Tier 2 ≈ 1,208 ≈ $0.014; Tier 3 ≈ 1,089 ≈ $0.012. Embedding cost is negligible at every rung and is not a reason to prefer any rung. **The costs that matter are fetch fragility (EUR-Lex WAF), PDF parse wall-clock, and review time** — all of which scale with documents fetched, which is the actual argument for stopping early. Estimates are calibrated on measured data (**NIST-style PDFs ≈ 0.8 chunks/page** — AI RMF: 48 pages → 38 chunks, exact; **~2,900 text chars / ~570 tokens per chunk** across all three current documents) and carry roughly ±40% uncertainty. Nothing in AC-10 depends on them being right.

#### The prepared ladder — fetch one rung, measure, then decide

**Rung 1 (Tier 1) — the only rung currently authorized to fetch.** Seven documents chosen purely for confusability with the existing corpus; they exist to be *mistaken* for it. This rung tests the hypothesis on its own.

| Document | Loader | Est. chunks | Why it competes |
|---|---|---:|---|
| NIST CSF 2.0 (CSWP 29) | nist_pdf | ~26 | Its **GOVERN** function collides by name with AI RMF Core's Govern — same word, different framework |
| NIST AI 600-1 (Generative AI Profile) | nist_pdf | ~51 | Literally an AI RMF *profile*; competes with "AI RMF Profiles" directly |
| NIST SP 800-37r2 (Risk Management Framework) | nist_pdf | ~146 | "Risk Management Framework" name collision with AI RMF |
| NIST SP 1270 (Bias in AI) | nist_pdf | ~69 | Competes with AI RMF "Fair – with Harmful Bias Managed" |
| GDPR — Reg. (EU) 2016/679 (`32016R0679`) | eurlex_html | ~120 | Art. 22 automated decision-making; DPIA vs. the AI Act's FRIA (Art. 27) |
| Machinery Reg. (EU) 2023/1230 (`32023R1230`) | eurlex_html | ~120 | Cross-referenced by AI Act Annex I; near-identical CE-marking/conformity vocabulary |
| Cyber Resilience Act — Reg. (EU) 2024/2847 (`32024R2847`) | eurlex_html | ~114 | Product cybersecurity + conformity assessment; competes with AI Act Art. 15 and Ch. III §5 |

**Rung 2 (Tier 2) — prepared, NOT authorized.** Fetched only if Rung 1 leaves recall@8 at 1.000, and only after that measurement is reviewed. Bulk with strong thematic overlap.

| Document | Loader | Est. chunks | Why it competes |
|---|---|---:|---|
| NIST SP 800-53r5 (Security & Privacy Controls) | nist_pdf | ~394 | Control-family governance language; highly self-similar internally |
| NIST SP 800-161r1 (C-SCRM) | nist_pdf | ~261 | Supply-chain risk; overlaps AI Act value-chain obligations (Art. 25) |
| NIST AI 100-2e2023 (Adversarial ML taxonomy) | nist_pdf | ~85 | Robustness/security of AI systems; overlaps AI Act Art. 15 |
| Medical Devices Reg. (EU) 2017/745 (`32017R0745`) | eurlex_html | ~240 | Sectoral conformity assessment + notified bodies, an AI Act Annex I law |
| DSA — Reg. (EU) 2022/2065 (`32022R2065`) | eurlex_html | ~138 | Systemic-risk assessment and auditing; competes with AI Act Ch. V |
| NIS2 — Dir. (EU) 2022/2555 (`32022L2555`) | eurlex_html | ~90 | Cybersecurity risk-management measures |

**Rung 3 (Tier 3) — prepared, NOT authorized, and no longer justified by a target.** These documents were originally selected to reach a 3,000-chunk floor that no longer exists. They are retained as available near-miss material for a third rung, but **the case for any of them is now "Rung 2 did not de-saturate", not "we need the volume."** If the ladder ever reaches here, re-review the selection on confusability grounds rather than fetching the list as drafted.

| Document | Loader | Est. chunks |
|---|---|---:|
| NIST SP 800-53Ar5 (Assessment Procedures) | nist_pdf | ~590 |
| NIST SP 800-30r1 (Risk Assessments) | nist_pdf | ~76 |
| NIST SP 800-171r3 (CUI in Nonfederal Systems) | nist_pdf | ~99 |
| NIST Privacy Framework 1.0 (CSWP 10) | nist_pdf | ~34 |
| Data Act — Reg. (EU) 2023/2854 (`32023R2854`) | eurlex_html | ~97 |
| Data Governance Act — Reg. (EU) 2022/868 (`32022R0868`) | eurlex_html | ~55 |
| GPSR — Reg. (EU) 2023/988 (`32023R0988`) | eurlex_html | ~69 |
| Market Surveillance — Reg. (EU) 2019/1020 (`32019R1020`) | eurlex_html | ~69 |

**Cumulative corpus size per rung, for fetch planning only:** after Rung 1 ≈ 1,004 chunks (10 documents) · after Rung 2 ≈ 2,212 (16 documents) · after Rung 3 ≈ 3,301 (24 documents). **These are not targets and no rung is justified by reaching one.** The expected outcome is that the ladder stops at Rung 1.

**CELEX ids and NIST document numbers above are unverified** — they are the identifiers to resolve at fetch time, not confirmed URLs. Verification is part of AC-12, before any fetching.

#### Baseline artifacts: one per corpus state, never overwritten *(amendment 2026-07-26)*

The before/after across rungs is the finding this amendment produces; a single mutable `retrieval_baseline.json` would destroy it on the first re-measure. Each measured corpus state writes an **immutable labeled artifact** under `evals/baselines/`:

```
evals/baselines/baseline-358-chunks.json     # pre-expansion, captured BEFORE anything is fetched
evals/baselines/baseline-1004-chunks.json    # after Rung 1
evals/baselines/baseline-2212-chunks.json    # after Rung 2, if reached
```

- **Named by measured chunk count**, so the file name states the corpus state it describes and cannot drift from it.
- **Each artifact self-describes its corpus:** document count, per-document chunk counts, the rung label, the git sha of the ingesting commit, the date, and the embedder identity — a number without its corpus state is not interpretable, and this is the file someone reads six months later.
- **Contents unchanged in kind** from SPEC-004 AC-6: recall@{1,3,8} per subset for both methods, MRR@8, discordant-pair counts, diversity distribution, stage-latency split, per-case ranks.
- **Immutable once written.** A re-measurement of the *same* corpus state overwrites its own file; a *different* corpus state never does.
- **`evals/retrieval_baseline.json` is retained as a copy of the most recent run** so SPEC-004's existing ACs and test wiring keep working, but it is explicitly *not* the record of any prior state.
- **Sequencing (binding): `baseline-358-chunks.json` is committed before the first fetch.** The pre-expansion measurement cannot be reconstructed after the corpus changes.

#### Cross-spec note (binding on SPEC-007): a multi-document corpus changes golden-set authoring *(amendment 2026-07-26)*

The 50-question golden set must be authored against these consequences, not retrofitted to them. **Scope note (second review):** the golden set stays scoped to answer correctness, groundedness, and refusal — the capabilities that need a human-verified *answer*. Retrieval significance is settled by the separate, cheaper **retrieval-only eval set** specified in SPEC-004 AC-6a's companion note, which needs only a human-verified *section label*. The three consequences below apply to the golden set; consequence 1 applies to both.

1. **Multi-source questions become possible, and must be deliberate.** With overlapping regulatory material, "what conformity assessment applies to a high-risk AI system that is also a medical device" legitimately draws on the AI Act *and* the MDR. SPEC-004's smoke set labels one `expected_section_prefix` per question, which cannot express this. **SPEC-007 must decide, before authoring:** whether a question carries a set of acceptable sections, and whether a hit means *any* expected section retrieved or *all* of them. Those are different metrics measuring different capabilities, and the choice cannot be made after the questions exist without re-labeling all of them.
2. **Unanswerable questions get harder to author well, and refusal is a scored capability.** With three documents, constructing a question the corpus genuinely cannot answer was easy. With overlapping compliance material the corpus plausibly covers far more ground, so a question *intended* as unanswerable may be answerable from a document the author did not have in mind. **Unanswerability must be verified against the whole corpus by retrieval**, not asserted from the authoring document's contents. This extends the existing binding note on dropped tables: refusal labels are claims about the corpus, and they must be tested like any other claim.
3. **Refusal labels rot when the corpus grows a rung.** A question correctly labeled unanswerable at Rung 1 may become answerable at Rung 2. **Every golden-set question records the corpus state it was validated against** (the `baseline-<N>-chunks` label), and unanswerable labels are re-validated whenever the corpus changes. A silently stale refusal label scores a correct answer as a failure — or worse, scores a hallucination as a correct refusal.

#### The one unavoidable code change: per-document metadata *(amendment 2026-07-26)*

This is **not** new parsing logic, but it is not zero work, and the constraint "existing loaders only" cannot be met without it. `eurlex_html.py` and `nist_pdf.py` currently hardcode their document identity as module constants — `SOURCE_URI`, `TITLE`, `DOC_LABEL`, and (PDF only) `FIRST_BODY_SECTION = "Executive Summary"`, which drives front-matter discard. One document per loader is baked in.

Minimum change, and the amendment's entire code scope:

- **`corpus/corpus.toml`** — a registry mapping each corpus filename to `{loader, source_uri, title, doc_label, doc_type, first_body_section?}`. Data, reviewable in one place, no logic.
- **`load_eurlex_html(path, meta)` / `load_nist_pdf(path, meta)`** — take metadata instead of reading module constants. Parsing logic is untouched.
- **`route_loader`** — resolves by registry entry rather than by content sniff for known files, falling back to the existing sniff.
- **`edgar_10k.py` is not modified.** It keeps its pinned constants; the registry simply names it for the one filing.

**`doc_type` mapping (SPEC-004 filter categories, kept at three):** every EU legal act → `regulation` (including the NIS2 *Directive* — the filter's purpose is "regulatory text vs. framework guidance vs. filing", and splitting directives from regulations would add a category no query distinguishes); every NIST publication → `standard`; the 10-K stays `filing`.

#### Fetch and the EUR-Lex WAF *(amendment 2026-07-26)*

The existing `fetch_corpus.py` behavior extends unchanged in kind, now per document:

- **NIST** — direct GET from `nvlpubs.nist.gov`. No auth, no challenge observed.
- **EUR-Lex** — the verified WAF JavaScript challenge (HTTP 202 + challenge body, 0 bytes of content) applies to *every* EUR-Lex document, not just the AI Act. Try direct GET; on the challenge response fall back to the Cellar content-negotiation endpoint `publications.europa.eu/resource/celex/{CELEX}` with `Accept: text/html`, `Accept-Language: en`. If both fail, print the manual-download instruction naming the CELEX id and exit non-zero. **With 11 EUR-Lex documents the WAF is now a bulk-fetch risk, not a single-file annoyance:** fetching must be resumable (skip files already present and passing the size guard) so a partial failure does not force refetching everything.
- **The > 100 KB size guard applies per file**, unchanged — it is what stops a challenge page from silently entering the corpus, and it is why today's failure mode (a 0-byte `eu-ai-act-2024-1689.html`) was caught. Documents legitimately smaller than 100 KB — CSF 2.0 and the Privacy Framework are plausible at ~26–34 chunks — need a per-document minimum in the registry rather than a global constant, or the guard produces false failures.

### New dependencies

`pypdf`, `beautifulsoup4`, `lxml`, `pysbd`, `openai` (embeddings only; generation adapter is a later spec). All runtime deps of the ingestion path. *(`tenacity` was planned and dropped — see Embedding.)* **The 2026-07-26 corpus amendment adds no dependencies** — that is a deliberate constraint on the document set, not a coincidence.

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
13. **The corpus expands until recall@8 de-saturates, measured one rung at a time — there is no chunk-count target** *(amendment 2026-07-26; restated after review)*. **The rejected alternative is this spec's own first draft**, which set ~3,300 chunks with a 3,000 floor and then chose Tier 3 to reach it. That reproduced the defect removed from SPEC-004 AC-6 — a threshold guessed before measurement, with work selected to satisfy the guess — and it contradicted this spec's own mechanism, since competitor density comes from Tier 1's confusability, not from bulk. **Corrected: fetch Rung 1, measure, stop if recall@8 < 1.000; escalate only on evidence.** Chunk counts survive only as fetch-planning estimates. Two further rejected alternatives, both still rejected: *synthesizing or duplicating text* to inflate the corpus (moves the count, teaches the retriever nothing, and makes every downstream metric an artifact); *shrinking k until recall stops saturating* (k=8 is a product decision driven by the generation context window, not a metric-tuning knob). **The stop condition is a corpus property only** — decided-question counts are fixed by eval-set size, not corpus size, and must never be used to justify another rung.

14. **Near-miss composition over raw size** *(amendment 2026-07-26)*. The documents are selected for *semantic competition* with the existing corpus, not for volume or topical breadth — name collisions (NIST CSF's GOVERN vs. AI RMF's Govern; SP 800-37's "Risk Management Framework" vs. "AI RMF"), shared legal machinery (CE marking, notified bodies, harmonised standards across the AI Act, Machinery, CRA, MDR, GPSR), and overlapping obligations (GDPR Art. 22 automated decisions vs. AI Act Art. 6/27). **This deliberately makes the eval set harder to score well on**, which is the point: a retrieval metric that cannot go down cannot demonstrate an improvement either. Expect measured recall to *drop* at each rung — that is success, not regression, and each rung's labeled baseline artifact must be read in that light. **A rung that leaves recall unchanged has added volume without difficulty**, which is the failure this decision guards against and a reason to re-examine the document selection rather than to fetch the next rung.

15. **Per-document metadata is data, not code — and it is the amendment's whole code surface** *(amendment 2026-07-26)*. Loaders currently hardcode document identity; a registry (`corpus/corpus.toml`) supplies it instead. **Flagged honestly: this does mean touching `eurlex_html.py` and `nist_pdf.py`**, so "existing loaders only" means *no new parsing logic*, not *no diff*. The boundary is explicit — a change that adds heading heuristics, a new structure signal, or a format branch is out of scope and the offending document is dropped instead. `edgar_10k.py` is not touched at all (decision 10 stands).

16. **Two structural risks that can invalidate parts of the document set; both are verified per rung, before that rung is fetched** *(amendment 2026-07-26)*.
   - **EUR-Lex HTML format drift across years.** `eurlex_html` was written against the AI Act's 2024 OJ rendering (`eli-subdivision` ids `rct_*`/`art_*`, `oj-ti-art`, `oj-sti-art`, `oj-ti-section-*`). EUR-Lex's markup has changed over the years, and the older proposed acts — **GDPR (2016), MDR (2017), Reg. 2019/1020** — are the ones most likely to render differently. Mitigation: probe **one old (GDPR) and one recent (CRA)** document through the loader *before* fetching the rest. If old-format documents do not parse, the EUR-Lex set contracts to 2022-and-later acts (DSA, NIS2, DGA, Data Act, Machinery, CRA, GPSR ≈ 683 chunks) and the shortfall is made up from NIST, which is format-stable. **Do not fix this with parsing logic** (non-goal above).
   - **Not every NIST PDF carries a bookmark outline.** `nist_pdf` is outline-driven; a PDF without one yields no sections. Mitigation: check the outline of each candidate PDF at fetch time and drop any that lack one. Large control catalogs (SP 800-53r5, SP 800-53Ar5) are the highest-value and highest-risk entries here — they are also where a *malformed* outline (hundreds of flat control-id bookmarks) could produce thousands of tiny sections rather than none, so the check is "has a usable hierarchy", not merely "has bookmarks".

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
- **AC-10 (de-saturation — the ladder's stop condition)** *(amendment 2026-07-26; restated after first review, clarified after second)* — after **each rung** is ingested, SPEC-004's `tests/test_retrieval_quality.py` is re-run and that rung's labeled baseline artifact written. The verdict, in the **primary tuning metric declared in SPEC-004 (`recall@8`)**:
  - **recall@8 = 1.000 for both methods → escalate. Mandatory**, not a judgment call: the metric is at its ceiling and cannot demonstrate an improvement.
  - **recall@8 < 1.000 → stopping is PERMITTED, not mandated.** The bare inequality is necessary but not sufficient — 0.99 is one miss in 26 with no usable headroom. Stopping requires a recorded judgment that the headroom is usable for the improvement loop; escalating requires the same. **Objectively testable part: the artifact and the recorded decision-with-numbers exist for every rung.** The judgment itself is reviewed, not thresholded.
  - **Headroom is assessed in the primary tuning metric, not a diagnostic one.** MRR@8 headroom under a saturated recall@8 does not satisfy this AC (SPEC-004 AC-6a). If SPEC-007 changes its primary metric, this check is re-run against the new one.
  - **Recorded either way:** recall@{1,3,8} per subset for both methods, MRR@8, discordant-pair counts, and the stop/escalate decision with its evidence. **No minimum recall value, chunk count, or document count is specified here, and none may be added retroactively to justify a rung already fetched.**
  - **This AC fails if a rung is fetched without the prior rung's measurement existing.** Each rung is justified by evidence, not by the plan.
  - **Decided-question counts are recorded but are explicitly NOT part of the stop condition.** Not because corpus growth fails to raise them — it probably does raise them — but because corpus is sized for realism and headroom while question sets are sized for power; sizing the corpus to make a secondary question answerable optimizes the wrong artifact (Key decision 13). Short decided-counts are answered by SPEC-004's retrieval-only eval set, never by another rung.
- **AC-11 (per-document metadata)** *(amendment 2026-07-26)* — every ingested document's `documents.source_uri`, `title`, and `doc_type` come from `corpus/corpus.toml`, not from a module constant: asserted by ingesting two different EUR-Lex fixtures through `eurlex_html` in one run and checking they land with *different* titles and source URIs. `doc_type` is `regulation` for every EU act (including the NIS2 Directive), `standard` for every NIST publication, `filing` for the 10-K. `edgar_10k.py` is byte-identical to its pre-amendment state (asserted by diff in review, not by test).
- **AC-12 (fetch, WAF, and pre-flight verification)** *(amendment 2026-07-26)* — `fetch_corpus` is **resumable**: a file already present and passing its size guard is skipped, so a WAF failure on document 9 of 11 does not refetch the first 8. Each EUR-Lex fetch tries the direct URL, falls back to the Cellar endpoint on the 202-challenge response, and on double failure exits non-zero naming the CELEX id and the manual-download path. Size guards are **per document** in the registry (a global 100 KB floor would falsely reject CSF 2.0 and the Privacy Framework). **Pre-flight, per rung, before that rung is fetched:** one old (GDPR) and one recent (CRA) EUR-Lex document parse to sane section counts under the unmodified `eurlex_html`, and every candidate NIST PDF in the rung is confirmed to carry a usable outline hierarchy — documents failing either check are dropped from the set rather than accommodated (Key decision 16). Probe output is reviewed before the rung's fetch is authorized.
- **AC-13 (baseline artifacts are immutable and self-describing)** *(amendment 2026-07-26)* — `evals/baselines/baseline-358-chunks.json` exists and is committed **before the first fetch**; each subsequent rung adds `baseline-<measured-chunk-count>-chunks.json` without modifying any existing artifact. Every artifact carries its corpus state: document count, per-document chunk counts, rung label, ingesting git sha, date, and embedder identity. Asserted by test: an artifact's recorded chunk count equals the sum of its per-document counts, and re-running a measurement against an unchanged corpus rewrites only that corpus state's own file (verified by comparing file mtimes/hashes of the other artifacts).
- **AC-14 (the test suite's own embedding spend is priced and bounded)** *(amendment 2026-08-02; owner-asked, CLAUDE.md rule 4's owner-asked clause — the request named the mechanism, the ceiling, and the verification)* — `tests/embedding_ledger.py` wraps `OpenAIEmbeddingClient.embed` for the whole session, prices every call at `EMBEDDING_USD_PER_MTOK` (the same constant the ingest report bills at — imported, never copied), attributes it to the test that made it, and **raises before the call that would cross a $0.0002 ceiling**. Measured full-suite spend on 2026-08-02 was 79 calls / 998 tokens / **$0.00002**, so the ceiling is 10× the observed cost. Objectively testable, in three parts:
  - **Bounding, not reporting.** The ceiling is checked *before* each call, so a runaway is stopped at the ceiling rather than described after it. **Verified live** (2026-08-02) by mutating a test into a loop of 200 real embedding calls: the run went red at call **18** having spent $0.0002034, and with `check()` removed the same loop **ran all 30 calls to completion and passed green** at $0.000339. The failure message names the offending nodeid.
  - **The override may only tighten.** `RAG_QA_TEST_EMBEDDING_CEILING_USD` lowers the ceiling and is ignored when it would raise it; a malformed value falls back to the default rather than raising, because a bad string in CI must not be what decides whether a spend guard is armed. Unit-tested in both directions.
  - **Arithmetic is CI-testable without a provider.** The ledger's pricing, attribution, ceiling, and override resolution are asserted in `tests/test_embedding_ledger.py` with no network and no API key — the guard has to be correct in the environment it protects and can never be exercised against a real provider there. A run that made no real call prints nothing.

  **Scope, stated rather than assumed:** this bounds **CI and local test runs only**. Serving-path embedding spend is still unmetered and uncapped; SPEC-006 Key decision 16's invoice clause carries the deferral and the numeric triggers that reopen it.
- **AC-2 restated per document** *(amendment 2026-07-26)* — AC-2's exact counts (113 articles / 180 recitals / 13 annexes) are **specific to the AI Act** and remain asserted for it. For each newly added document the equivalent assertion is a recorded expected section count in the registry, checked on ingest; a document whose parsed section count deviates by more than ±10% from its recorded expectation fails ingestion rather than entering the corpus silently.

## Test plan

`tests/test_ingest_loaders.py`, `test_ingest_chunker.py`, `test_ingest_pipeline.py`, `test_fetch_corpus.py` — async where DB-touching, reusing SPEC-002's binding fixture pattern (session-scoped engine, savepoint rollback).

Two fixture tiers, so CI never needs the network:

- **Synthetic fixtures** (committed, small, hand-built to reproduce measured structure): a EUR-Lex-style HTML snippet with `eli-subdivision`/`oj-ti-art`/layout-table point lists; an EDGAR-style snippet with duplicate Item headings, `ix:` tags, a cp1252 byte (0x92), one numeric and one narrative table; a 3-page PDF generated in-fixture with pypdf carrying an outline and a repeated header line. These back AC-1, AC-3, AC-4 (invariant logic), AC-5, AC-6, AC-8 in CI.
- **Real-corpus tests** (`@pytest.mark.skipif(not CORPUS_PRESENT, ...)`): AC-2's exact counts and AC-4's invariants over the full documents run locally against `corpus/`; CI skips them. Rationale: EUR-Lex WAF makes networked CI fetch non-deterministic.

Embedding client is faked everywhere (deterministic 1536-dim vectors, call recorder for AC-5/AC-6); no API key in CI. Pipeline tests run against the dockerized Postgres (chunk insert exercises the real vector/tsv columns). Migration test (AC-7) follows SPEC-002's scratch-database pattern.

Test order follows the workflow rule: these tests are written from the ACs above and committed before/with the implementation, in the same commit series referencing SPEC-003.

### Corpus expansion testing *(amendment 2026-07-26)*

The two-tier split holds, with the expansion landing almost entirely in the real-corpus tier — CI must not grow a 24-document fetch dependency.

- **Synthetic tier (CI).** A *second* EUR-Lex-style fixture with different `eli-subdivision` content, a different title and a different source URI, so AC-11's "two documents through one loader" assertion runs without the network. A minimal second bookmarked PDF fixture likewise. Registry parsing (`corpus/corpus.toml` → metadata) and the per-document size-guard logic are unit-tested with no I/O. `fetch_corpus` resumability (AC-12) is tested with mocked transport: a pre-existing valid file is not re-requested, and a 202-challenge response triggers the Cellar URL.
- **Real-corpus tier (local, `skipif`).** AC-10's de-saturation measurement and the per-document section-count checks of restated AC-2 run only where the corpus exists. **AC-10 is a measurement per rung, recorded rather than asserted green/red** — the recorded distribution is what the stop/escalate decision is made from, and it is reported in that rung's PR.
- **Pre-flight probes are scripts, not tests** — the GDPR/CRA format probe and the NIST outline check (AC-12) run before fetching and gate whether a document enters `corpus/corpus.toml` at all. Their output is pasted into the PR so the document set is reviewed against what actually parsed, not against what was hoped. **Probes run per rung, against that rung's documents only.**
- **Artifact immutability (AC-13)** is a cheap unit test over `evals/baselines/` — internal consistency of each artifact, and that a re-measure touches only its own corpus state's file. No corpus needed.

**Sequencing (binding, per rung — the ladder is the process, not just the plan):**

1. **Once, before anything is fetched:** commit `evals/baselines/baseline-358-chunks.json` capturing the pre-expansion state (AC-13). It cannot be reconstructed later.
2. **Once:** registry + loader metadata change (`corpus/corpus.toml`, metadata argument), with the synthetic-tier tests green. No documents fetched yet.
3. **Per rung:** pre-flight probes for that rung's documents → review probe output and the document set → fetch → ingest → re-run SPEC-004 retrieval quality → write that rung's labeled baseline artifact → **record the AC-10 stop/escalate verdict with its numbers**.
4. **Stop as soon as recall@8 < 1.000.** Escalation to the next rung requires the prior rung's recorded measurement showing recall@8 still exactly 1.000.

Fetching a rung before the previous rung's measurement exists violates AC-10. Fetching anything before step 1 destroys the pre-expansion record.

#### Ladder log — the recorded verdict per rung (AC-10)

**Rung 0 — pre-expansion. Measured 2026-08-02 at `386a344`. Artifact: `evals/baselines/baseline-358-chunks.json`.**

| | Value |
|---|---|
| Corpus | 358 chunks / 3 documents (AI RMF 38 · AI Act 201 · NVDA 10-K 119) |
| `recall@8`, hybrid | **1.000** |
| `recall@8`, vector-only | **1.000** |
| Discordant pairs at k=8 | **0** (hybrid-only 0 · vector-only 0 · both 26 · neither 0) |
| McNemar exact, two-sided | p = 1.0 — **vacuous, and recorded as vacuous**: with zero discordant pairs there is no data in the test at all |
| `recall@1`, hybrid / vector-only | 0.769 / 0.885 |
| `MRR@8`, hybrid / vector-only | 0.865 / 0.942 |

**Verdict: ESCALATE.** Neither pre-registered condition is met, and the second is not close: `recall@8 < 1.000` is false, and `discordant_pairs ≥ 25` fails at **0 of 25**. This is the completely saturated state SPEC-004 Key decision 12a described, now measured rather than inferred, and it is the "before" every later rung is read against.

**Two things the numbers say that the stop condition does not, both recorded and neither acted on:**
- **The paraphrase regression is still there and is still not evidence.** Vector-only leads hybrid at k=1 overall (0.885 vs 0.769) and on paraphrase (0.917 vs 0.583), while hybrid leads on citation (0.929 vs 0.857) — the same shape SPEC-004 Key decision 12 recorded as noise on 26 questions. At k=8, which is the *pre-registered* metric, there is nothing to see, because there is nothing left to separate. Reading a k=1 result while the pre-registered metric is saturated is exactly the substitution Key decision 12 of SPEC-007 forbids.
- **`neither` is 0.** Every question is answered by both methods. This is not a retrieval result; it is a statement that the corpus contains no competitor for any of these 26 questions, which is the thing Key decision 14's near-miss selection exists to change.

**Re-measured under prereg-2** *(same day, `evals/baselines/baseline-358-chunks-prereg-2.json`)*: identical numbers, since the question set has not changed yet — but the comparison is now recorded as **inconclusive** with its reason ("0 discordant pairs is below the 6 at which the exact test can reject at all") rather than as a failed gate, and the run carries a **declared deviation** for having 26 questions against a pre-registered 120. The prereg-1 artifact is retained as the record of a superseded pre-registration and is never compared to a prereg-2 rung. See SPEC-007 Key decision 12, amendment 1.

#### Rung 1 pre-flight probe — measured 2026-08-02, artifact `evals/probes/probe-rung-1.json`

Run under the narrow approval of 2026-08-02: the seven documents are a **probe set, not an ingest set**. They were fetched, parsed, chunked and embedded into a **scratch database seeded with a copy of the corpus**, measured, and removed. The corpus database was never written — verified after the run at 358 chunks / 3 documents, and the scratch database was dropped.

**Format probe: all seven parse.** Every candidate produced a hierarchy at least two levels deep under its unmodified loader, so none is dropped on format grounds. Chunk counts landed within −23 % / +25 % of the fetch-planning estimates. Two observations that are not failures but belong in the record: `nist-sp-800-37r2` yields only 51 sections for 180 chunks and its first two section titles are both "Revision 2", so its outline extraction is the weakest of the set; and `machinery-regulation` produced six chunker warnings for annex chunks exceeding `target_max` after the breadcrumb.

**Competition probe — the ranking criterion is competition per chunk, and it separates the set by a factor of fifty.** `appearances` counts candidate chunks reaching the top 8 across the 26 questions; `q` is how many distinct questions it reached; `density` is appearances per chunk added.

| Candidate | Chunks | Appearances (hybrid) | q | **Density** | Gold displaced (h/v) |
|---|---:|---:|---:|---:|---:|
| nist-csf-2-0 | 20 | 9 | 2 | **0.450** | 0 / 0 |
| nist-ai-600-1 | 57 | 22 | 5 | **0.386** | 0 / 0 |
| nist-sp-1270 | 86 | 25 | 6 | **0.291** | 0 / 0 |
| cyber-resilience-act | 111 | 13 | 3 | 0.117 | 0 / 0 |
| gdpr | 117 | 10 | 4 | 0.085 | 0 / 0 |
| nist-sp-800-37r2 | 180 | 14 | 5 | 0.078 | 0 / 0 |
| machinery-regulation | 112 | **1** | 1 | **0.009** | 0 / 0 |

**All seven together: 683 chunks added (2.9× the corpus), and `recall@8` does not move.** Hybrid 1.000 → 1.000, vector-only 1.000 → 1.000, **discordant pairs 0**, `neither` 0, **zero gold chunks displaced on either arm**. Across all 26 questions only **four** gold ranks moved at all on hybrid (mean +1.75 places) and **one** on vector-only.

**Finding 1 — Key decision 13's hypothesis is falsified, and this is the measurement that does it.** That decision predicted "Tier 1 alone (~646 new chunks) de-saturates recall@8 and Tiers 2–3 are never fetched." 683 chunks of deliberately confusable material moved the primary metric by exactly nothing. **Fetching Rung 2 on the current reasoning would be repeating a disproved experiment at twice the size**, and Key decision 14 already says what a rung that leaves recall unchanged means: re-examine the selection, do not fetch the next one.

**Finding 2 — the binding constraint may be the question set, not the corpus.** Zero displacements is expected on a hard corpus; *four rank movements out of 26* is not. The gold chunks are not winning narrowly, they are winning by a distance that 683 competing chunks barely dented. The 26 smoke questions were authored from the corpus with an expected section in hand, which makes each gold chunk a lexical bullseye — and no amount of *other* text competes with a bullseye. **This is a hypothesis, not a finding, and it is falsifiable**: if the effect is the questions, a set of harder questions de-saturates at the current corpus size, and that costs no fetch at all. It should be tested before another rung is fetched, because it is cheaper and because SPEC-007 KD-12 amendment 1 already requires authoring 94 more retrieval questions for unrelated reasons.

**Finding 3 — size and competition are nearly uncorrelated, which is the argument for this probe existing.** The largest candidate (`nist-sp-800-37r2`, 180 chunks) ranks sixth of seven on density. The smallest (`nist-csf-2-0`, 20 chunks) ranks first. `machinery-regulation` adds 112 chunks and reaches the top 8 **once, for one question** — a document that costs money to embed and moves no number, indistinguishable from a good candidate on any size-based measure and separated from one immediately here.

**No recommendation is made about the selection**, which is the owner's review under the narrow approval. What the numbers support is that the set is not uniform: three candidates compete an order of magnitude harder per chunk than the other four.

**Cost:** 878,578 embedding tokens, **$0.0176**. Half of that was waste — each candidate was embedded twice, once alone and once in the combined run — and the probe now caches vectors by `source_uri`, so a re-run costs ~$0.0088.

**The ladder stops here pending approval, not pending evidence.** The evidence for escalating to Rung 1 is complete and recorded above. What is missing is the approval to fetch — see the Status block: the corpus-expansion amendment is still **DRAFT**, and its own text says nothing is fetched until it is approved and Rung 1's probe output is reviewed.
