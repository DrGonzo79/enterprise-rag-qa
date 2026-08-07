"""Render the discordant cases as a standalone verification worksheet.

**Why this exists.** McNemar conditions on the discordant set, so the `c` cases
*are* the result and the 100 concordant questions contribute nothing to `p`.
`human_verified: false` therefore does not bound the finding evenly — it bounds
it almost entirely through these cases. Verifying them converts a bound on the
result into a bound on the questions that carry none of it.

Emits `evals/discordant-20.md`: question, gold label, and the full corpus text
the gold points at, readable without the repository. Set `RAG_QA_WORKSHEET_HTML`
to also render an HTML reading surface at that path — the same data, two
renderings from one pass, so they cannot drift.

**What hybrid returned instead is in an appendix, deliberately.** Seeing the
competing section before judging whether the gold is right would anchor the
judgement toward "hybrid's answer was also reasonable", which is a different
question and one worth asking second.

Usage:
    uv run python -m scripts.discordant_worksheet
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rag_qa.env import load_env
from rag_qa.ingest.embedder import OpenAIEmbeddingClient
from rag_qa.retrieval.service import Retriever
from scripts.query_plan import EVAL_SERVER_SETTINGS
from scripts.section_match import matches_section

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULT = REPO_ROOT / "evals" / "confirmatory-result.json"
CASES = REPO_ROOT / "evals" / "retrieval_confirmatory.jsonl"
OUT = REPO_ROOT / "evals" / "discordant-20.md"
K = 8

load_env()
CORPUS_URL = os.environ.get(
    "RAG_QA_CORPUS_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag"
)


def _hit(rank: Any) -> bool:
    return isinstance(rank, int) and rank <= K


HTML_HEAD = """<title>The discordant cases — verification worksheet</title>
<style>
/* Sans = the harness's apparatus. Serif = corpus text under judgement.
   Mono = an identifier. That distinction is the page's whole job. */
:root {
  --paper: #f5f6f8;
  --panel: #ffffff;
  --ink: #15191e;
  --muted: #5c6672;
  --rule: #d7dbe1;
  --accent: #1f5f6b;
  --flag: #8a5a12;
  --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #0f1318; --panel: #161b22; --ink: #e4e8ee; --muted: #98a3b0;
    --rule: #2a323c; --accent: #63b3c0; --flag: #d5a45c;
  }
}
:root[data-theme="dark"] {
  --paper: #0f1318; --panel: #161b22; --ink: #e4e8ee; --muted: #98a3b0;
  --rule: #2a323c; --accent: #63b3c0; --flag: #d5a45c;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--paper); color: var(--ink);
  font-family: var(--sans); font-size: 17px; line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
.masthead, main, .tail { max-width: 46rem; margin: 0 auto; padding: 0 1.5rem; }
.masthead { padding-top: 4rem; padding-bottom: 2rem; }
.eyebrow {
  font-size: 0.75rem; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--accent); margin: 0 0 0.75rem;
}
h1 {
  font-family: var(--serif); font-size: clamp(2rem, 5vw, 2.9rem); line-height: 1.1;
  margin: 0 0 1rem; text-wrap: balance; font-weight: 600; letter-spacing: -0.01em;
}
.standfirst { font-size: 1.05rem; color: var(--muted); margin: 0 0 2rem; }
.standfirst em { color: var(--ink); font-style: italic; }
.figures {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
  gap: 1px; margin: 0 0 2rem; background: var(--rule);
  border: 1px solid var(--rule); border-radius: 3px; overflow: hidden;
}
.figures > div { background: var(--panel); padding: 0.9rem 1rem; }
.figures dt {
  font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted); margin: 0 0 0.3rem;
}
.figures dd {
  margin: 0; font-family: var(--mono); font-size: 1.25rem;
  font-variant-numeric: tabular-nums; color: var(--ink);
}
.sensitivity {
  border-left: 3px solid var(--flag); padding: 0.1rem 0 0.1rem 1rem;
  color: var(--muted); font-size: 0.95rem; margin: 0;
}
.sensitivity strong { color: var(--ink); }
.progress {
  position: sticky; top: 0; z-index: 5; background: var(--paper);
  border-bottom: 1px solid var(--rule); padding: 0.6rem 1.5rem;
  font-size: 0.8rem; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--muted); font-variant-numeric: tabular-nums;
}
.case { border-top: 1px solid var(--rule); padding: 2.5rem 0 1rem; }
.case h2 {
  display: flex; align-items: baseline; gap: 0.6rem; flex-wrap: wrap;
  font-size: 0.9rem; font-weight: 500; margin: 0 0 1rem; color: var(--muted);
}
.num {
  font-family: var(--mono); font-size: 0.95rem; color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.id { color: var(--ink); font-size: 0.9rem; }
.tags { font-size: 0.78rem; letter-spacing: 0.05em; text-transform: uppercase; }
.question {
  font-family: var(--serif); font-size: 1.3rem; line-height: 1.4;
  margin: 0 0 1.5rem; text-wrap: pretty;
}
.meta { margin: 0 0 1.75rem; display: grid; gap: 0.85rem; }
.meta dt {
  font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 0.2rem;
}
.meta dd { margin: 0; font-size: 0.95rem; }
.mono { font-family: var(--mono); font-size: 0.85em; }
.path { color: var(--accent); overflow-wrap: anywhere; }
.sourcelabel {
  font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted); margin: 0 0 0.6rem;
}
.source {
  font-family: var(--serif); font-size: 0.98rem; line-height: 1.65;
  background: var(--panel); border: 1px solid var(--rule);
  border-left: 3px solid var(--accent); border-radius: 2px;
  margin: 0 0 1rem; padding: 1.1rem 1.3rem; overflow-x: auto;
}
.source p { margin: 0 0 0.8rem; }
.source p:last-child { margin-bottom: 0; }
.source .path {
  font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.02em;
  color: var(--muted); margin-bottom: 0.9rem;
}
.verdict {
  display: inline-flex; align-items: center; gap: 0.5rem; cursor: pointer;
  font-size: 0.8rem; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--muted); padding: 0.4rem 0;
}
.verdict input { accent-color: var(--accent); width: 1rem; height: 1rem; }
.verdict:has(:checked) { color: var(--accent); }
.tail { padding-top: 3rem; padding-bottom: 2rem; border-top: 1px solid var(--rule); }
.tail h2 { font-family: var(--serif); font-size: 1.4rem; margin: 0 0 0.75rem; }
.tail p { color: var(--muted); font-size: 0.95rem; }
.tail ul, .tail ol { padding-left: 1.2rem; }
.tail li { margin-bottom: 0.6rem; font-size: 0.95rem; }
.inner { margin-top: 0.4rem; }
details summary {
  cursor: pointer; font-size: 0.85rem; letter-spacing: 0.05em;
  text-transform: uppercase; color: var(--accent); padding: 0.5rem 0;
}
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 40rem) { body { font-size: 16px; } .masthead { padding-top: 2.5rem; } }
</style>
"""

HTML_TAIL = """<script>
(function () {
  var boxes = document.querySelectorAll(".judged");
  var tally = document.getElementById("tally");
  function count() {
    var n = 0;
    boxes.forEach(function (b) { if (b.checked) n += 1; });
    tally.textContent = String(n);
  }
  boxes.forEach(function (b) { b.addEventListener("change", count); });
  count();
})();
</script>
"""


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def render_html(
    primary: dict[str, Any],
    vector_wins: list[dict[str, Any]],
    hybrid_wins: list[dict[str, Any]],
    cases: dict[str, dict[str, Any]],
    chunks: list[tuple[str, int, str]],
    returned: dict[str, list[str]],
) -> str:
    """The reading surface. Three type roles carry the one distinction that matters.

    **Sans is my apparatus, serif is the corpus, mono is an identifier.** The
    reader's whole task is deciding whether my label matches the source text, so
    the page has to make "written by the harness" and "quoted from the statute"
    separable at a glance, before either is read.
    """
    parts: list[str] = []
    add = parts.append

    add(HTML_HEAD)
    add('<header class="masthead">')
    add('<p class="eyebrow">SPEC-007 &middot; confirmatory retrieval comparison</p>')
    add("<h1>The discordant cases</h1>")
    add(
        '<p class="standfirst">McNemar conditions on the discordant set, so these '
        f"{primary['n_discordant']} pairs <em>are</em> the result and the "
        f"{primary['n'] - primary['n_discordant']} concordant questions contribute nothing "
        'to <span class="mono">p</span>. Every gold label below is '
        '<span class="mono">human_verified: false</span>. The task is to confirm or '
        "reject each one.</p>"
    )
    add('<dl class="figures">')
    for label, value in (
        ("b &mdash; hybrid only", primary["b_hybrid_only"]),
        ("c &mdash; vector-only", primary["c_vector_only"]),
        ("n discordant", primary["n_discordant"]),
        ("p, two-sided exact", primary["p_two_sided_exact"]),
    ):
        add(f"<div><dt>{label}</dt><dd>{value}</dd></div>")
    add("</dl>")
    add(
        '<p class="sensitivity"><strong>How far <span class="mono">p</span> moves if golds '
        'are wrong.</strong> Treating each correction as removing one <span class="mono">c'
        "</span> pair: 20&ndash;3 gives 0.000488, 17&ndash;3 gives 0.0026, 14&ndash;3 gives "
        "0.0127, 12&ndash;3 gives 0.0352 &mdash; still significant &mdash; and 11&ndash;3 "
        "gives 0.0574, which is not. The result survives eight corrections and fails at the "
        'ninth. A correction that <em>flips</em> a case to <span class="mono">b</span> '
        "costs two steps, not one.</p>"
    )
    add("</header>")

    add('<div class="progress" role="status" aria-live="polite">')
    add('<span id="tally">0</span> of 20 judged')
    add("</div>")

    add("<main>")
    for index, row in enumerate(vector_wins, start=1):
        case = cases[row["id"]]
        gold = case["expected_section_prefix"]
        add('<article class="case">')
        add(
            f'<h2><span class="num">{index}</span> <span class="mono id">{_esc(case["id"])}'
            f'</span> <span class="tags">{_esc(case["shape"])} &middot; '
            f"{_esc(case['document'])}</span></h2>"
        )
        add(f'<p class="question">{_esc(case["question"])}</p>')
        add('<dl class="meta">')
        add(f'<div><dt>Gold label</dt><dd class="mono">{_esc(gold)}</dd></div>')
        if "also_contains" in case:
            add(
                "<div><dt>Other half of the span</dt>"
                f'<dd class="mono">{_esc(case["also_contains"])}</dd></div>'
            )
        add(f"<div><dt>Why that label</dt><dd>{_esc(case['label_reason'])}</dd></div>")
        add(
            "<div><dt>Outcome</dt><dd>Vector-only found it at rank "
            f"<strong>{row['vector_rank']}</strong>. Hybrid did not return it.</dd></div>"
        )
        add("</dl>")
        matching = [chunk for chunk in chunks if matches_section(gold, chunk[0])]
        add(
            f'<p class="sourcelabel">Corpus text under that label &mdash; '
            f"{len(matching)} chunk{'s' if len(matching) != 1 else ''}</p>"
        )
        for path, _, body in matching:
            add('<blockquote class="source">')
            add(f'<p class="mono path">{_esc(path)}</p>')
            for paragraph in body.split("\n"):
                if paragraph.strip():
                    add(f"<p>{_esc(paragraph.strip())}</p>")
            add("</blockquote>")
        add('<label class="verdict"><input type="checkbox" class="judged"> Judged</label>')
        add("</article>")
    add("</main>")

    add('<section class="tail">')
    add(f"<h2>The {len(hybrid_wins)} cases hybrid found and vector-only did not</h2>")
    add("<p>The other side of the same 23.</p>")
    add("<ul>")
    for row in hybrid_wins:
        case = cases[row["id"]]
        add(
            f'<li><span class="mono">{_esc(case["id"])}</span> &mdash; '
            f'{_esc(case["question"])} <span class="mono path">'
            f"{_esc(case['expected_section_prefix'])}</span>, hybrid rank "
            f"{row['hybrid_rank']}</li>"
        )
    add("</ul>")
    add("</section>")

    add('<section class="tail appendix">')
    add("<h2>What hybrid returned instead</h2>")
    add(
        "<p><strong>Read this after judging the golds, not before.</strong> Seeing the "
        "competing section first anchors the judgement toward <em>hybrid&rsquo;s answer was "
        "also reasonable</em>, which is a real question and a different one from <em>is this "
        "gold correct</em>.</p>"
    )
    add("<details><summary>Show hybrid&rsquo;s top three per case</summary>")
    add("<ol>")
    for row in vector_wins:
        add(f'<li><span class="mono">{_esc(row["id"])}</span><ol class="inner">')
        for path in returned[row["id"]]:
            add(f'<li class="mono path">{_esc(path)}</li>')
        add("</ol></li>")
    add("</ol></details>")
    add("</section>")
    add(HTML_TAIL)
    return "\n".join(parts)


def _load() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    cases: dict[str, dict[str, Any]] = {}
    for line in CASES.read_text(encoding="utf-8").splitlines():
        if line:
            case = json.loads(line)
            cases[case["id"]] = case
    return result, cases


async def main() -> int:
    if not RESULT.exists():
        print("no confirmatory result yet", file=sys.stderr)
        return 2
    result, cases = _load()

    vector_wins = [
        row
        for row in result["per_case_primary"]
        if _hit(row["vector_rank"]) and not _hit(row["hybrid_rank"])
    ]
    hybrid_wins = [
        row
        for row in result["per_case_primary"]
        if _hit(row["hybrid_rank"]) and not _hit(row["vector_rank"])
    ]

    engine = create_async_engine(CORPUS_URL, connect_args={"server_settings": EVAL_SERVER_SETTINGS})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (await session.execute(text("SELECT section_path, ordinal, text FROM chunks"))).all()
    chunks = [(str(r[0]), int(r[1]), str(r[2])) for r in rows]
    chunks.sort(key=lambda r: r[1])

    embedder = OpenAIEmbeddingClient()
    retriever = Retriever(factory, embedder)
    returned: dict[str, list[str]] = {}
    for row in vector_wins:
        hits = await retriever.retrieve(cases[row["id"]]["question"], k=3)
        returned[row["id"]] = [chunk.section_path for chunk in hits]
    await engine.dispose()

    primary = result["primary"]
    out: list[str] = []
    add = out.append

    add("# The discordant cases — verification worksheet")
    add("")
    add(
        f"**{primary['n_discordant']} discordant pairs decide the confirmatory result** "
        f"(b = {primary['b_hybrid_only']}, c = {primary['c_vector_only']}, "
        f"p = {primary['p_two_sided_exact']}, two-sided exact, alpha = {primary['alpha']}). "
        "McNemar conditions on the discordant set, so the other "
        f"{primary['n'] - primary['n_discordant']} questions contribute nothing to `p`. "
        "These are the cases that carry the finding."
    )
    add("")
    add(
        "**Every gold label below is `human_verified: false`.** It was written from corpus "
        "text and checked against corpus text by the authoring agent, not by the repository "
        "owner. The task here is to confirm or reject each one."
    )
    add("")
    add(
        "**How far `p` moves if golds are wrong.** Treating each correction as removing one "
        "`c` pair: 20–3 gives p = 0.000488, 17–3 gives 0.0026, 14–3 gives 0.0127, "
        "**12–3 gives 0.0352 (still significant)**, and **11–3 gives 0.0574 — not "
        "significant**. So the result survives up to **eight** corrections and fails at the "
        "ninth. A correction that *flips* a case to `b` rather than removing it costs more "
        "than one: 4–19 is two steps, not one."
    )
    add("")
    add("---")
    add("")
    add(f"## Part 1 — the {len(vector_wins)} cases vector-only found and hybrid did not (`c`)")
    add("")
    add(
        "In every one of these the gold chunk is **absent from hybrid's top 8 entirely**, "
        "not merely ranked lower."
    )
    add("")

    for index, row in enumerate(vector_wins, start=1):
        case = cases[row["id"]]
        add(f"### {index}. `{case['id']}` — {case['shape']}, {case['document']}")
        add("")
        add(f"**Question.** {case['question']}")
        add("")
        add(f"**Gold label.** `{case['expected_section_prefix']}`")
        if "also_contains" in case:
            add("")
            add(f"**Also contains (the other half of the span).** `{case['also_contains']}`")
        add("")
        add(f"**Why that label was chosen.** {case['label_reason']}")
        add("")
        add(f"**Vector-only found it at rank {row['vector_rank']}. Hybrid did not return it.**")
        add("")
        gold = case["expected_section_prefix"]
        matching = [chunk for chunk in chunks if matches_section(gold, chunk[0])]
        add(f"**Corpus text under that label** — {len(matching)} chunk(s):")
        add("")
        for path, _, body in matching:
            add(f"> **`{path}`**")
            add(">")
            for paragraph in body.split("\n"):
                if paragraph.strip():
                    add(f"> {paragraph.strip()}")
            add("")
        add("---")
        add("")

    add(f"## Part 2 — the {len(hybrid_wins)} cases hybrid found and vector-only did not (`b`)")
    add("")
    add("Listed for completeness; they are the other side of the same 23.")
    add("")
    for row in hybrid_wins:
        case = cases[row["id"]]
        add(
            f"- `{case['id']}` ({case['shape']}, {case['document']}) — "
            f"*{case['question']}* → `{case['expected_section_prefix']}`, "
            f"hybrid rank {row['hybrid_rank']}"
        )
    add("")
    add("---")
    add("")
    add("## Appendix — what hybrid returned instead")
    add("")
    add(
        "**Read this after judging the golds, not before.** Seeing the competing section "
        'first anchors the judgement toward *"hybrid\'s answer was also reasonable"*, which '
        'is a real question and a different one from *"is this gold correct"*.'
    )
    add("")
    for index, row in enumerate(vector_wins, start=1):
        add(f"**{index}. `{row['id']}`** — hybrid's top 3:")
        for rank, path in enumerate(returned[row["id"]], start=1):
            add(f"  {rank}. `{path}`")
        add("")

    html_path = os.environ.get("RAG_QA_WORKSHEET_HTML")
    if html_path:
        Path(html_path).write_text(
            render_html(primary, vector_wins, hybrid_wins, cases, chunks, returned),
            encoding="utf-8",
        )
        print(f"written: {html_path}")

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    rendered = "\n".join(out)
    print(f"written: {OUT.relative_to(REPO_ROOT)} ({len(rendered)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
