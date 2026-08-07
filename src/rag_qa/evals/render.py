"""Render a report to Markdown, without flattening it (SPEC-007 Key decision 13).

**"Flatten" is the failure this module is written against**, and it is the one a
renderer will not announce: dropping a caveat looks exactly like a tidy summary.
So the rules are mechanical rather than tasteful —

- every figure prints its `claim` **and** its `not_a_claim`, both in full;
- every proportion prints its interval beside it, because 20/23 rendered alone
  reads as certainty;
- `outcome` is printed as its own line, never left to be inferred from `arms`;
- deviations are printed **in full, each with its reason**, never as a count;
- `not_a_claim` is never truncated, wrapped to a summary, or moved to a footnote.

The acceptance test is the retrieval comparison of 2026-08-05 — the hardest
finding this harness is likely to be handed, with seven deviations and a
`not_a_claim` carrying a counter-record and a statement that the alternative
cannot be tested.
"""

from rag_qa.evals.report import Report, ScalarFigure


def _interval(bounds: tuple[float, float]) -> str:
    return f"[{bounds[0]:.4g}, {bounds[1]:.4g}]"


def _warrant(claim: str, not_a_claim: str) -> list[str]:
    """Both, in full, always adjacent.

    Adjacency matters: a `claim` rendered here and a `not_a_claim` collected
    into a footnote section is the flattening this exists to prevent, and it
    would satisfy any check that merely asked whether the string appears.
    """
    return [
        f"**Claim.** {claim}",
        "",
        f"**Not a claim.** {not_a_claim}",
        "",
    ]


def render_markdown(report: Report) -> str:
    out: list[str] = []
    add = out.append

    add("# Evaluation report")
    add("")
    add(
        f"`{report.git_sha}` · {report.created_at} · "
        f"{report.methodology.corpus.chunks} chunks · "
        f"generator `{report.generator_identity}` · prompt `{report.prompt_version}` · "
        f"cost ${report.cost_usd}"
    )
    add("")

    add("## Figures")
    add("")
    for figure in report.figures:
        if isinstance(figure, ScalarFigure):
            add(f"### {figure.metric}")
            add("")
            add(
                f"**{figure.value:.4g}** {_interval(figure.interval)} · "
                f"n = {figure.n} · decided = {figure.decided}"
            )
            add("")
            out.extend(_warrant(figure.claim, figure.not_a_claim))
        else:
            add(f"### {figure.metric} — {' vs '.join(figure.arms)}")
            add("")
            add("| | value |")
            add("|---|---:|")
            for arm, value in figure.arms.items():
                add(f"| {arm} | {value:.4g} |")
            first, second = list(figure.arms)
            add(f"| b ({first} only) | {figure.b} |")
            add(f"| c ({second} only) | {figure.c} |")
            add(f"| n discordant | {figure.n_discordant} |")
            add(f"| n | {figure.n} |")
            add(f"| p ({figure.test}, {figure.sidedness}) | {figure.p:.6g} |")
            add(f"| alpha | {figure.alpha} |")
            add("")
            # Both on their own lines. The interval is the parameter the test is
            # about; the outcome is never left to be read off `arms`.
            add(f"**Interval (95%).** {_interval(figure.interval)}")
            add("")
            add(f"**Outcome.** {figure.outcome}")
            add("")
            out.extend(_warrant(figure.claim, figure.not_a_claim))

    add("## Methodology")
    add("")
    add(report.methodology.summary)
    add("")
    add(f"**Authoring.** {report.methodology.authoring}")
    add("")
    corpus = report.methodology.corpus
    add(
        f"**Corpus.** {corpus.chunks} chunks across "
        f"{len(corpus.documents)} documents: {', '.join(corpus.documents)}. "
        f"Primary metric {corpus.primary_metric_value:.4g} — "
        f"**de-saturated: {str(corpus.desaturated).lower()}** (derived, not declared)."
    )
    add("")

    prereg = report.methodology.preregistration
    add("### Pre-registration")
    add("")
    add(f"Registered {prereg.preregistered_at}.")
    add("")
    add(f"- primary metric: `{prereg.primary_metric}` at k = {prereg.k}")
    add(f"- test: `{prereg.test}`, {prereg.sidedness}, alpha = {prereg.alpha}")
    add(f"- conclusive when: {prereg.conclusive_when}")
    add("")

    add("### Deviations")
    add("")
    if not report.methodology.deviations:
        add("None recorded.")
        add("")
    else:
        # In full, each with its reason. A count here would be the flattening
        # that hides seven judgement calls behind the digit 7.
        for index, deviation in enumerate(report.methodology.deviations, start=1):
            add(f"**{index}. {deviation.field}**")
            add("")
            add(f"- pre-registered: {deviation.preregistered}")
            add(f"- actual: {deviation.actual}")
            add(f"- reason: {deviation.reason}")
            add("")

    add("### Limitations")
    add("")
    for limitation in report.methodology.limitations:
        add(f"- {limitation}")
    add("")

    return "\n".join(out)
