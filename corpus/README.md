# Corpus

Source documents are **not committed** — they're public, sizable, and better
fetched than redistributed. Run `python -m scripts.fetch_corpus` (SPEC-003) or
download manually using the links below.

| Document | Source | Format | License |
|---|---|---|---|
| NIST AI Risk Management Framework 1.0 (NIST AI 100-1) | [nvlpubs.nist.gov](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf) | PDF | US Government work, public domain |
| EU AI Act — Regulation (EU) 2024/1689 | [EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202401689) | HTML | © European Union, reuse permitted with attribution |
| Annual report (Form 10-K) | [SEC EDGAR](https://www.sec.gov/edgar/search/) | HTML | Public filing |

Chosen for structural variety — a sectioned PDF, a deeply nested regulation
with articles and recitals, and a long table-dense filing — which is what makes
heading-aware chunking a real problem rather than a trivial one.

**Note for the fetch script:** SEC EDGAR requires a descriptive `User-Agent`
header containing contact information. Requests without one are rejected.