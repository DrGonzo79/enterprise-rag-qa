"""Fetch registry documents into corpus/ (SPEC-003 AC-12).

Registry-driven and **resumable**: a file already present and passing its own
size guard is skipped, so a failure on document 6 of 7 does not refetch the
first five. Failures are collected and reported together rather than aborting on
the first one — a partial rung with a named list of what is missing is more
useful than a traceback on whichever document happened to come first.

Two provider quirks, both verified 2026-08-02 and both worth stating because
each produces a *plausible* wrong answer rather than an error:

- **`nvlpubs.nist.gov` returns 404 to HEAD and 200 to GET.** A pre-flight
  existence check written the obvious way reports every NIST document missing.
- **EUR-Lex sits behind an AWS WAF that answers with a 202 challenge page.** It
  was not challenging on 2026-08-02, but the fallback stays: the challenge page
  is a few KB of valid HTML and would be chunked and embedded as a document
  without the size guard.

Usage:
    uv run python -m scripts.fetch_corpus --rung rung-1 --purpose probe
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from rag_qa.ingest.registry import RegisteredDocument, for_rung, load

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "corpus" / "corpus.toml"
CORPUS_DIR = REPO_ROOT / "corpus"
USER_AGENT = "enterprise-rag-qa (thompsn79@gmail.com)"
TIMEOUT = 120.0


@dataclass
class Outcome:
    document: RegisteredDocument
    status: str  # "skipped" | "fetched" | "failed"
    detail: str
    byte_size: int = 0


def looks_like_challenge(status_code: int, content: bytes, minimum: int) -> bool:
    return status_code == 202 or len(content) < minimum or b"awsWaf" in content[:4096]


def _attempt(client: httpx.Client, url: str, minimum: int) -> tuple[bytes | None, str]:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    if looks_like_challenge(response.status_code, response.content, minimum):
        return None, f"challenge or truncated response ({len(response.content)} bytes)"
    return response.content, f"{len(response.content):,} bytes"


def fetch_one(client: httpx.Client, document: RegisteredDocument) -> Outcome:
    target = CORPUS_DIR / document.filename
    # Resumability is a size check, not an existence check: a half-written file
    # from an interrupted run exists and is useless, and skipping it on presence
    # alone would make that failure permanent and silent.
    if target.exists() and target.stat().st_size >= document.min_bytes:
        return Outcome(document, "skipped", "already present", target.stat().st_size)

    content, detail = _attempt(client, document.url, document.min_bytes)
    if content is None and document.fallback_url:
        content, fallback_detail = _attempt(client, document.fallback_url, document.min_bytes)
        detail = f"{detail}; fallback: {fallback_detail}"
    if content is None:
        return Outcome(document, "failed", detail)

    target.write_bytes(content)
    return Outcome(document, "fetched", detail, len(content))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rung", default="rung-1")
    parser.add_argument(
        "--purpose",
        choices=("probe", "ingest"),
        default="probe",
        help=(
            "`probe` evaluates a candidate; `ingest` prepares it for the corpus. "
            "Probe-candidate documents refuse `ingest` — the Rung 1 set is "
            "approved as a probe set, not an ingest set."
        ),
    )
    args = parser.parse_args(argv)

    documents = for_rung(load(REGISTRY), args.rung)
    if not documents:
        print(f"no documents registered for {args.rung}", file=sys.stderr)
        return 2

    # The approval gate, enforced rather than documented. A probe-candidate is
    # approved to be *evaluated*; nothing here may prepare one for the corpus.
    blocked = [d.id for d in documents if args.purpose == "ingest" and d.is_probe_only]
    if blocked:
        print(
            f"refusing --purpose ingest for probe-candidate documents: {', '.join(blocked)}.\n"
            "The Rung 1 set is approved as a PROBE set only (SPEC-003 status block). "
            "Change `status` in corpus/corpus.toml once the ladder is approved.",
            file=sys.stderr,
        )
        return 3

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    outcomes: list[Outcome] = []
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=TIMEOUT
    ) as client:
        for document in documents:
            outcome = fetch_one(client, document)
            outcomes.append(outcome)
            print(f"{outcome.status:<8} {document.id:<24} {outcome.detail}")

    failed = [o for o in outcomes if o.status == "failed"]
    if failed:
        print(f"\n{len(failed)} document(s) could not be fetched:", file=sys.stderr)
        for outcome in failed:
            print(f"  {outcome.document.id}: {outcome.detail}", file=sys.stderr)
            print(f"    manual download: {outcome.document.url}", file=sys.stderr)
            print(f"    save as: corpus/{outcome.document.filename}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
