"""System prompt and context rendering (SPEC-005 Interface).

PROMPT_VERSION is recorded on every query_log row: without it, logged answers
cannot be attributed to the prompt that produced them. Changing SYSTEM_PROMPT
without bumping PROMPT_VERSION fails a test (AC-12).

**v2 (2026-08-07) adds the trailing verdict — Key decision 7 amendment 1.** The
first-line token is kept, because it is the whole reason a streaming client can
render a refusal before the prose arrives. It is now explicitly *provisional*:
measured on 20 unanswerable questions under v1, **13 answers whose body declined
carried a first line of `ANSWERED`**. The model commits on the first token and
then reasons; the trailing token is the same commitment made after the reasoning
is on the page, and it is the authoritative one.
"""

from collections.abc import Sequence

from rag_qa.retrieval.types import RetrievedChunk

PROMPT_VERSION = "v2"

ANSWERED_TOKEN = "ANSWERED"
INSUFFICIENT_TOKEN = "INSUFFICIENT_EVIDENCE"

SYSTEM_PROMPT = f"""You answer questions about compliance documents using ONLY the \
numbered excerpts provided with each question.

Begin every response with a single verdict token on its own line, before any other \
text:

- `{ANSWERED_TOKEN}` — the excerpts support an answer.
- `{INSUFFICIENT_TOKEN}` — they do not.

Then, on the following lines, write the answer or the explanation.

**End every response by repeating the verdict token on a line of its own, as the \
final line, with nothing else on that line.** The first one is a provisional signal \
so a reader can start rendering; the last one is your considered verdict, made after \
you have written out the evidence. If they differ, the last one is the one that \
counts, and changing your mind is correct rather than an error to hide — write the \
verdict you actually hold, not the one you opened with.

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
