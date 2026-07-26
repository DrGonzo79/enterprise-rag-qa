"""Format-specific loaders. All format knowledge is quarantined here
(SPEC-003 Key decision 1); the chunker and pipeline are format-blind."""

from rag_qa.ingest.loaders.edgar_10k import load_edgar_10k
from rag_qa.ingest.loaders.eurlex_html import load_eurlex_html
from rag_qa.ingest.loaders.nist_pdf import load_nist_pdf

__all__ = ["load_edgar_10k", "load_eurlex_html", "load_nist_pdf"]
