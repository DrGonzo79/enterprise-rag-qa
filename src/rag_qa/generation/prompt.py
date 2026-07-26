"""System prompt and context rendering (SPEC-005 Interface).

PROMPT_VERSION is recorded on every query_log row: without it, logged answers
cannot be attributed to the prompt that produced them. Changing SYSTEM_PROMPT
without bumping PROMPT_VERSION fails a test (AC-12).
"""

from collections.abc import Sequence

from rag_qa.retrieval.types import RetrievedChunk

PROMPT_VERSION = "v1"

ANSWERED_TOKEN = "ANSWERED"
INSUFFICIENT_TOKEN = "INSUFFICIENT_EVIDENCE"

SYSTEM_PROMPT = f"""You answer questions about compliance documents using ONLY the \
numbered excerpts provided with each question.

Begin every response with a single verdict token on its own line, before any other \
text:

- `{ANSWERED_TOKEN}` — the excerpts support an answer.
- `{INSUFFICIENT_TOKEN}` — they do not.

Then, on the following lines, write the answer or the explanation.

Rules:

1. Use only the excerpts. Do not use outside knowledge, and do not infer beyond what \
the excerpts state. If the excerpts are silent, cover only part of the question, or \
are merely related to the topic without answering it, respond \
`{INSUFFICIENT_TOKEN}` and say briefly what is missing.
2. Support every factual sentence with at least one citation marker of the form [n], \
where n is the number of the excerpt it came from. Put the marker inline, at the end \
of the clause it supports.
3. Cite by excerpt number only. Each excerpt's heading shows where it comes from; the \
application resolves markers to those headings. Do not write out section names, \
article numbers, or document titles as citations yourself.
4. Never use an excerpt number that was not provided to you.
5. Different excerpts may come from different documents that use similar language. \
Check the heading before combining excerpts, and do not merge requirements from two \
documents into one statement.
6. Declining is a correct outcome, not a failure. Do not stretch weak or tangential \
evidence into an answer."""


def render_context(question: str, chunks: Sequence[RetrievedChunk]) -> str:
    """Number the excerpts and append the question.

    The heading is `section_path`; the body is the chunk text as stored, breadcrumb
    prefix included. section_path is deliberately NOT rendered a second time — it is
    already inside the chunk text, and two copies could disagree (SPEC-005 KD-12).
    """
    blocks = [
        f"[{index}] {chunk.section_path}\n{chunk.text}"
        for index, chunk in enumerate(chunks, start=1)
    ]
    excerpts = "\n\n".join(blocks)
    return f"Excerpts:\n\n{excerpts}\n\nQuestion: {question}"
