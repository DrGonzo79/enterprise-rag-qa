"""Corpus registry parsing and validation (SPEC-003 amendment, step 2).

Pure — no network, no database. The registry's whole job is to be wrong loudly
rather than quietly: every field it carries either identifies a document in the
corpus or gates whether it may enter one, and both failure modes are silent.
"""

from pathlib import Path

import pytest

from rag_qa.ingest.registry import RegistryError, for_rung, load, parse

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "corpus" / "corpus.toml"

MINIMAL = """
[[document]]
id = "a"
status = "ingested"
rung = "rung-0"
loader = "nist_pdf"
filename = "a.pdf"
url = "https://example.invalid/a.pdf"
min_bytes = 1000
title = "A"
doc_label = "A"
doc_type = "standard"
source_uri = "https://example.invalid/a.pdf"
"""


def _second(**overrides: str) -> str:
    """A second entry that differs from MINIMAL in every unique field except the
    ones the caller deliberately collides."""
    text = MINIMAL.replace('id = "a"', 'id = "b"')
    text = text.replace('filename = "a.pdf"', 'filename = "b.pdf"')
    text = text.replace(
        'source_uri = "https://example.invalid/a.pdf"',
        'source_uri = "https://example.invalid/b.pdf"',
    )
    for key, value in overrides.items():
        line = next(ln for ln in text.splitlines() if ln.startswith(f"{key} = "))
        text = text.replace(line, f'{key} = "{value}"')
    return text


def test_the_committed_registry_parses() -> None:
    documents = load(REGISTRY)
    assert len(documents) >= 10
    assert {d.id for d in for_rung(documents, "rung-0")} == {
        "nist-ai-rmf",
        "eu-ai-act",
        "nvda-10k",
    }


def test_every_rung_1_document_is_probe_only() -> None:
    """The approval is narrow and the registry is where it is enforced: the Rung 1
    set is approved as a probe set, not an ingest set. A document that quietly
    reads `ingested` would enter the corpus on the next routine ingest with no
    review at all."""
    rung_1 = for_rung(load(REGISTRY), "rung-1")
    assert len(rung_1) == 7
    not_probe_only = [d.id for d in rung_1 if not d.is_probe_only]
    assert not not_probe_only, f"approved for probing only, but marked ingestable: {not_probe_only}"


def test_rung_0_documents_match_what_is_actually_ingested() -> None:
    """The registry's metadata replaces the loaders' module constants, so a
    mismatch here would relabel documents that are already in the corpus —
    including `doc_label`, which becomes the first element of every
    `section_path` and is what gold answers are matched against."""
    from rag_qa.ingest.loaders import edgar_10k, eurlex_html, nist_pdf

    by_id = {d.id: d for d in load(REGISTRY)}
    for doc_id, module in (
        ("nist-ai-rmf", nist_pdf),
        ("eu-ai-act", eurlex_html),
        ("nvda-10k", edgar_10k),
    ):
        assert by_id[doc_id].source_uri == module.SOURCE_URI, doc_id
        assert by_id[doc_id].title == module.TITLE, doc_id
        assert by_id[doc_id].doc_label == module.DOC_LABEL, doc_id


def test_size_guards_are_per_document_and_positive() -> None:
    """A single global floor would reject the smaller NIST publications while
    passing a WAF challenge page served in place of a 700 KB regulation."""
    documents = load(REGISTRY)
    assert all(d.min_bytes > 0 for d in documents)
    assert len({d.min_bytes for d in documents}) > 1, (
        "a single shared floor is not a per-document guard"
    )


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("loader", "magic_loader", "unknown loader"),
        ("doc_type", "memo", "unknown doc_type"),
        ("status", "approved", "unknown status"),
    ],
)
def test_unknown_enum_values_are_rejected(field: str, value: str, fragment: str) -> None:
    text = MINIMAL.replace(
        {
            "loader": 'loader = "nist_pdf"',
            "doc_type": 'doc_type = "standard"',
            "status": 'status = "ingested"',
        }[field],
        f'{field} = "{value}"',
    )
    with pytest.raises(RegistryError, match=fragment):
        parse(text)


def test_a_missing_required_key_names_the_document() -> None:
    with pytest.raises(RegistryError, match="document 'a' is missing required key 'min_bytes'"):
        parse(MINIMAL.replace("min_bytes = 1000\n", ""))


def test_a_non_positive_size_guard_is_rejected() -> None:
    """`min_bytes = 0` disables the guard while looking like a configured value —
    which is how a WAF challenge page becomes a corpus document."""
    with pytest.raises(RegistryError, match="non-positive min_bytes"):
        parse(MINIMAL.replace("min_bytes = 1000", "min_bytes = 0"))


@pytest.mark.parametrize(
    ("field", "collision"),
    [
        ("id", {"id": "a"}),
        ("filename", {"filename": "a.pdf"}),
        ("source_uri", {"source_uri": "https://example.invalid/a.pdf"}),
    ],
)
def test_duplicates_are_rejected(field: str, collision: dict[str, str]) -> None:
    """Two documents sharing a `source_uri` is not a cosmetic problem: the ingest
    upsert deletes by `source_uri`, so the second would silently replace the
    first — a corpus of N documents that quietly holds N-1."""
    with pytest.raises(RegistryError, match=f"duplicate {field}"):
        parse(MINIMAL + _second(**collision))


def test_an_empty_registry_is_rejected() -> None:
    with pytest.raises(RegistryError, match="declares no documents"):
        parse("# nothing here\n")


# --- the ingest exclusion (the approval gate, once the file is on disk) --------


def _corpus_dir(tmp_path: Path, registry_text: str | None) -> Path:
    (tmp_path / "approved.pdf").write_bytes(b"%PDF-1.4 approved")
    (tmp_path / "probe.pdf").write_bytes(b"%PDF-1.4 probe")
    if registry_text is not None:
        (tmp_path / "corpus.toml").write_text(registry_text, encoding="utf-8")
    return tmp_path


TWO_DOCS = MINIMAL.replace('filename = "a.pdf"', 'filename = "approved.pdf"') + _second(
    filename="probe.pdf", status="probe-candidate"
)


def test_discover_excludes_probe_only_documents(tmp_path: Path) -> None:
    """Probing a candidate means fetching it into corpus/, and `discover` returns
    every loadable file there — so without this, a routine `python -m
    rag_qa.ingest` would sweep seven unapproved documents into the corpus as a
    side effect of having evaluated them. A gate that lives only in the fetch
    script is not a gate once the file is on disk."""
    from rag_qa.ingest.pipeline import discover
    from rag_qa.ingest.types import IngestConfig

    found = discover(_corpus_dir(tmp_path, TWO_DOCS), IngestConfig())
    assert [p.name for p in found] == ["approved.pdf"]


def test_no_registry_excludes_nothing(tmp_path: Path) -> None:
    """A fresh checkout and every synthetic fixture directory have no registry.
    Nothing is registered as probe-only there, so nothing is excluded."""
    from rag_qa.ingest.pipeline import discover
    from rag_qa.ingest.types import IngestConfig

    found = discover(_corpus_dir(tmp_path, None), IngestConfig())
    assert sorted(p.name for p in found) == ["approved.pdf", "probe.pdf"]


def test_a_broken_registry_raises_rather_than_ingesting_everything(tmp_path: Path) -> None:
    """The opposite of the case above, and the distinction is the whole guard. A
    registry that exists and does not parse means the file deciding what may be
    ingested is broken; continuing would ingest whatever is on disk, which is
    precisely the documents it was meant to hold back."""
    from rag_qa.ingest.pipeline import discover
    from rag_qa.ingest.types import IngestConfig

    broken = _corpus_dir(tmp_path, TWO_DOCS.replace('loader = "nist_pdf"', 'loader = "wat"', 1))
    with pytest.raises(RegistryError):
        discover(broken, IngestConfig())


# --- the pilot set is excluded from the confirmatory set (SPEC-007 KD-12 am. 2) ---


def test_the_pilot_set_shares_no_ids_with_any_other_eval_set() -> None:
    """A pilot that sizes an analysis and then contributes cases to it is the
    same substitution KD-12 amendment 1 exists to prevent, wearing a new name:
    the cases that set the threshold would be among the cases judged against it,
    and they were selected for being hard."""
    import json

    evals = REPO_ROOT / "evals"
    pilot = {
        json.loads(line)["id"]
        for line in (evals / "retrieval_pilot.jsonl").read_text().splitlines()
        if line.strip()
    }
    assert pilot, "the pilot set is empty"
    for other in evals.glob("*.jsonl"):
        if other.name == "retrieval_pilot.jsonl":
            continue
        ids = {json.loads(line)["id"] for line in other.read_text().splitlines() if line.strip()}
        assert not (pilot & ids), f"pilot ids leaked into {other.name}: {sorted(pilot & ids)}"


def test_every_pilot_case_records_why_its_label_is_what_it_is() -> None:
    """The labels are machine-drafted and human-unverified, so the reasoning has
    to travel with them — SPEC-004's binding note is that an auto-labelled
    retrieval set measures the labeler. A reviewer cannot confirm a label whose
    justification was never written down."""
    import json

    cases = [
        json.loads(line)
        for line in (REPO_ROOT / "evals" / "retrieval_pilot.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert 12 <= len(cases) <= 15, f"pre-registered size is 12-15, found {len(cases)}"
    for case in cases:
        assert case["label_reason"].strip(), case["id"]
        assert case["human_verified"] is False, (
            f"{case['id']} claims human verification; only the owner may set that"
        )
        assert case["shape"] in {"wrong-lexical-match", "near-miss", "spans-two-sections"}, case[
            "id"
        ]
        # The recipe forbids naming the section: a question carrying its own
        # answer's citation is the lexical bullseye the pilot exists to avoid.
        assert "Article" not in case["question"], f"{case['id']} names an article in the question"
