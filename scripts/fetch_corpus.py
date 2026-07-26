"""Fetch the three corpus documents into corpus/ (SPEC-003 Interface).

- EDGAR requires a descriptive User-Agent with contact info.
- EUR-Lex sits behind an AWS WAF JavaScript challenge (verified 2026-07-25):
  plain HTTP gets a 202 challenge page. We try the direct URL, fall back to
  the Cellar content-negotiation endpoint, and otherwise print manual
  instructions. A minimum-size check keeps a challenge page from silently
  passing as corpus.
"""

import sys
from pathlib import Path

import httpx

USER_AGENT = "enterprise-rag-qa (thompsn79@gmail.com)"
MIN_BYTES = 100_000

NIST_URL = "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf"
EDGAR_URL = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"
EURLEX_URL = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202401689"
CELLAR_URL = "http://publications.europa.eu/resource/celex/32024R1689"

EURLEX_MANUAL_HELP = (
    "EUR-Lex could not be fetched over plain HTTP (AWS WAF challenge).\n"
    f"Download manually in a browser from:\n  {EURLEX_URL}\n"
    "and save as corpus/eu-ai-act-2024-1689.html"
)


def _looks_like_challenge(response: httpx.Response) -> bool:
    return (
        response.status_code == 202
        or len(response.content) < MIN_BYTES
        or b"awsWaf" in response.content[:4096]
    )


def _save(path: Path, content: bytes, source: str) -> None:
    if len(content) < MIN_BYTES:
        raise RuntimeError(
            f"{source}: got {len(content)} bytes (< {MIN_BYTES}); refusing to write {path.name}"
        )
    path.write_bytes(content)
    print(f"{path.name}: {len(content):,} bytes from {source}")


def fetch_nist(client: httpx.Client, corpus: Path) -> None:
    response = client.get(NIST_URL)
    response.raise_for_status()
    _save(corpus / "nist-ai-rmf-100-1.pdf", response.content, NIST_URL)


def fetch_edgar(client: httpx.Client, corpus: Path) -> None:
    response = client.get(EDGAR_URL)
    response.raise_for_status()
    _save(corpus / "nvda-10k-2026.htm", response.content, EDGAR_URL)


def fetch_eurlex(client: httpx.Client, corpus: Path) -> None:
    target = corpus / "eu-ai-act-2024-1689.html"
    response = client.get(EURLEX_URL)
    if not _looks_like_challenge(response):
        _save(target, response.content, EURLEX_URL)
        return

    print(f"EUR-Lex direct fetch blocked (HTTP {response.status_code}); trying Cellar…")
    fallback = client.get(
        CELLAR_URL,
        headers={"Accept": "text/html", "Accept-Language": "en"},
    )
    if fallback.status_code == 200 and len(fallback.content) >= MIN_BYTES:
        _save(target, fallback.content, CELLAR_URL)
        return

    raise RuntimeError(EURLEX_MANUAL_HELP)


def main() -> int:
    corpus = Path(__file__).resolve().parent.parent / "corpus"
    corpus.mkdir(exist_ok=True)
    failures: list[str] = []

    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=120
    ) as client:
        for fetch in (fetch_nist, fetch_edgar, fetch_eurlex):
            try:
                fetch(client, corpus)
            except Exception as exc:
                failures.append(f"{fetch.__name__}: {exc}")

    for failure in failures:
        print(f"\nFAILED — {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
