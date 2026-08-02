"""fetch_corpus tests (SPEC-003 AC-1, AC-12) — mocked transport, no network.

Rewritten when the fetcher became registry-driven. The old tests asserted
per-document functions (`fetch_edgar`, `fetch_eurlex`) that no longer exist; the
behaviours they protected — a contact User-Agent, the WAF fallback, and never
writing a short response — are all still asserted here, against the new shape.
"""

from pathlib import Path

import httpx
import pytest
from scripts import fetch_corpus
from scripts.fetch_corpus import USER_AGENT, fetch_one, looks_like_challenge, main

from rag_qa.ingest.registry import RegisteredDocument

MIN_BYTES = 1000
BIG = b"x" * (MIN_BYTES + 1)


def _document(**overrides: object) -> RegisteredDocument:
    base: dict[str, object] = {
        "id": "doc",
        "status": "probe-candidate",
        "rung": "rung-1",
        "loader": "eurlex_html",
        "filename": "doc.html",
        "url": "https://primary.invalid/doc",
        "min_bytes": MIN_BYTES,
        "title": "Doc",
        "doc_label": "Doc",
        "doc_type": "regulation",
        "source_uri": "https://primary.invalid/doc",
    }
    base.update(overrides)
    return RegisteredDocument(**base)  # type: ignore[arg-type]


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(
        transport=handler, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )


@pytest.fixture(autouse=True)
def corpus_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fetch_corpus, "CORPUS_DIR", tmp_path)
    return tmp_path


def test_the_request_carries_a_contact_user_agent(corpus_dir: Path) -> None:
    """EDGAR requires it and NIST tolerates it; a fetcher without contact info in
    the UA is the kind of thing that gets a project blocked rather than warned."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, content=BIG)

    with _client(httpx.MockTransport(handler)) as client:
        outcome = fetch_one(client, _document())

    assert outcome.status == "fetched"
    assert "@" in seen[0]


def test_a_challenge_falls_back_and_then_succeeds(corpus_dir: Path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if "primary" in str(request.url):
            return httpx.Response(202, content=b"<script>window.awsWafCookie</script>")
        return httpx.Response(200, content=BIG)

    with _client(httpx.MockTransport(handler)) as client:
        outcome = fetch_one(client, _document(fallback_url="https://fallback.invalid/doc"))

    assert requested == ["https://primary.invalid/doc", "https://fallback.invalid/doc"]
    assert outcome.status == "fetched"
    assert (corpus_dir / "doc.html").read_bytes() == BIG


def test_a_short_response_is_never_written(corpus_dir: Path) -> None:
    """A WAF challenge page is a few KB of valid HTML. Without the size guard it
    is chunked, embedded, and ingested as a document (SPEC-003 AC-1)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"tiny")

    with _client(httpx.MockTransport(handler)) as client:
        outcome = fetch_one(client, _document())

    assert outcome.status == "failed"
    assert not (corpus_dir / "doc.html").exists()


def test_size_guards_are_per_document(corpus_dir: Path) -> None:
    """The same 200 KB response passes a small document's guard and fails a large
    one's. A single global floor cannot do both, which is why the floor lives
    beside the document."""
    body = b"y" * 200_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    with _client(httpx.MockTransport(handler)) as client:
        small = fetch_one(client, _document(id="small", filename="s.html", min_bytes=100_000))
        large = fetch_one(client, _document(id="large", filename="l.html", min_bytes=900_000))

    assert small.status == "fetched"
    assert large.status == "failed"


def test_a_present_and_valid_file_is_not_refetched(corpus_dir: Path) -> None:
    """Resumability: a failure on document 6 of 7 must not refetch the first
    five."""
    (corpus_dir / "doc.html").write_bytes(BIG)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=BIG)

    with _client(httpx.MockTransport(handler)) as client:
        outcome = fetch_one(client, _document())

    assert outcome.status == "skipped"
    assert calls == []


def test_a_truncated_file_on_disk_is_refetched(corpus_dir: Path) -> None:
    """Resumability keys on the size guard, not on existence. A half-written file
    from an interrupted run exists and is useless, and skipping it on presence
    alone would make that failure permanent and silent."""
    (corpus_dir / "doc.html").write_bytes(b"partial")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=BIG)

    with _client(httpx.MockTransport(handler)) as client:
        outcome = fetch_one(client, _document())

    assert outcome.status == "fetched"
    assert calls == ["https://primary.invalid/doc"]
    assert (corpus_dir / "doc.html").read_bytes() == BIG


def test_probe_candidates_refuse_to_be_fetched_for_ingestion() -> None:
    """The approval gate, enforced rather than documented. The Rung 1 set is
    approved as a probe set; nothing may prepare one for the corpus, and a gate
    that lives only in prose is not a gate."""
    code = main(["--rung", "rung-1", "--purpose", "ingest"])
    assert code == 3


def test_the_challenge_detector_reads_all_three_signals() -> None:
    assert looks_like_challenge(202, BIG, MIN_BYTES)
    assert looks_like_challenge(200, b"short", MIN_BYTES)
    assert looks_like_challenge(200, b"<script>awsWaf" + BIG, MIN_BYTES)
    assert not looks_like_challenge(200, BIG, MIN_BYTES)
