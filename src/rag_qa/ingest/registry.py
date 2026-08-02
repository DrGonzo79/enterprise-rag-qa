"""The corpus registry (SPEC-003 amendment, step 2).

Per-document metadata in one file instead of module constants. The constants
were fine for three documents and become wrong at the fourth: `nist_pdf.py`
carries `TITLE`, `SOURCE_URI` and `DOC_LABEL` for the AI RMF specifically, so a
second NIST PDF loaded through it would enter the corpus claiming to be the AI
RMF — and `DOC_LABEL` is the first element of every `heading_path`, which means
the mislabelling reaches `section_path`, the field the evaluation set matches
gold answers against.

**Size guards are per document, not global.** A single 100 KB floor would falsely
reject the smaller NIST publications while passing an AWS WAF challenge page
served in place of a 700 KB regulation. The floor has to be sized to the document
it protects, which means it lives beside the document.

This module is pure: parsing and validation, no I/O beyond reading the registry
file, so it is unit-testable in CI where nothing is ever fetched.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

LOADERS = frozenset({"nist_pdf", "eurlex_html", "edgar_10k"})
DOC_TYPES = frozenset({"standard", "regulation", "filing"})
# `ingested` is in the corpus now; `probe-candidate` is approved for probing only
# and has NOT been approved for ingestion (SPEC-003 status block).
STATUSES = frozenset({"ingested", "probe-candidate"})


class RegistryError(ValueError):
    """Raised on a malformed registry, naming the document."""


@dataclass(frozen=True)
class RegisteredDocument:
    id: str
    status: str
    rung: str
    loader: str
    filename: str
    url: str
    min_bytes: int
    title: str
    doc_label: str
    doc_type: str
    source_uri: str
    competes_with: str = ""
    fallback_url: str = ""
    estimated_chunks: int = 0

    @property
    def is_probe_only(self) -> bool:
        return self.status == "probe-candidate"


def _require(raw: dict[str, object], key: str, doc_id: str) -> object:
    if key not in raw:
        raise RegistryError(f"document {doc_id!r} is missing required key {key!r}")
    return raw[key]


def parse(text: str) -> list[RegisteredDocument]:
    payload = tomllib.loads(text)
    entries = payload.get("document", [])
    if not entries:
        raise RegistryError("registry declares no documents")

    documents: list[RegisteredDocument] = []
    for raw in entries:
        doc_id = str(raw.get("id", "<unnamed>"))
        fields = {
            key: _require(raw, key, doc_id)
            for key in (
                "id",
                "status",
                "rung",
                "loader",
                "filename",
                "url",
                "min_bytes",
                "title",
                "doc_label",
                "doc_type",
                "source_uri",
            )
        }
        document = RegisteredDocument(
            **fields,  # type: ignore[arg-type]
            competes_with=str(raw.get("competes_with", "")),
            fallback_url=str(raw.get("fallback_url", "")),
            estimated_chunks=int(raw.get("estimated_chunks", 0)),
        )
        if document.loader not in LOADERS:
            raise RegistryError(f"document {doc_id!r} names unknown loader {document.loader!r}")
        if document.doc_type not in DOC_TYPES:
            raise RegistryError(f"document {doc_id!r} names unknown doc_type {document.doc_type!r}")
        if document.status not in STATUSES:
            raise RegistryError(f"document {doc_id!r} names unknown status {document.status!r}")
        if document.min_bytes <= 0:
            raise RegistryError(f"document {doc_id!r} has a non-positive min_bytes")
        documents.append(document)

    for field in ("id", "filename", "source_uri"):
        seen: dict[str, str] = {}
        for document in documents:
            value = getattr(document, field)
            if value in seen:
                raise RegistryError(
                    f"duplicate {field} {value!r} in {seen[value]!r} and {document.id!r}"
                )
            seen[value] = document.id
    return documents


def load(path: Path) -> list[RegisteredDocument]:
    return parse(path.read_text(encoding="utf-8"))


def for_rung(documents: list[RegisteredDocument], rung: str) -> list[RegisteredDocument]:
    return [d for d in documents if d.rung == rung]
