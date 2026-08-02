"""In-process counters, rendered as Prometheus text (SPEC-006 KD-9).

**No database.** The obvious implementation aggregates `query_log`, which is the
authoritative ledger — but a scrape every 15s taking one of ten connections is
monitoring competing with serving for the scarcest resource, and a growing
aggregate makes scrape latency grow with history. Counters are per-replica and
reset on restart, which is correct for Prometheus: `sum()` across instances and
`rate()`'s counter-reset detection handle both.

Division of labor: this answers "what is happening now, per replica";
`query_log` answers "what did this cost since the beginning", offline.
"""

from collections import Counter
from decimal import Decimal
from typing import TYPE_CHECKING

from rag_qa.api.conditions import spec_for

if TYPE_CHECKING:
    from rag_qa.api.budget import BudgetSnapshot

LATENCY_BUCKETS_SECONDS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    def __init__(self) -> None:
        self.requests: Counter[tuple[str, int]] = Counter()
        self.verdicts: Counter[str] = Counter()
        self.latency_buckets: Counter[float] = Counter()
        self.latency_count = 0
        self.latency_sum = 0.0
        # The embedding round-trip, separately from the request it is part of.
        # SPEC-004 AC-8 amendment 4 withdrew the end-to-end p50 assertion because
        # it was a bound on a third party's latency; the degraded windows that
        # assertion was accidentally detecting are real and have a consequence
        # here (SPEC-006 KD-10 amendment 5: a 20x drop in the shed threshold), so
        # they are watched where they can be acted on instead. Emitted from
        # process start so `absent()` on it means "not reporting", never "quiet".
        self.embed_latency_buckets: Counter[float] = Counter()
        self.embed_latency_count = 0
        self.embed_latency_sum = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = Decimal("0")
        # SPEC-008: the failure signal. A budget trip, a shed, and an embedder
        # mismatch are all `status="503"` on rag_qa_requests_total, so the single
        # most consequential state of this deployment -- the demo has stopped
        # answering -- could not be read from the endpoint an operator reads.
        self.errors: Counter[str] = Counter()
        self.budget_trips: Counter[str] = Counter()
        # Separate from `budget_trips`, and separate from the headroom gauges.
        # A trip means the demo is out of money until a UTC boundary; pressure
        # means it is out of *uncommitted* money for a few seconds. A gauge
        # sampled every 15s cannot see a 3s spike at all, so a deployment
        # refusing a third of its arrivals to reservation pressure looks
        # identical to a healthy one on `budget_remaining` -- a counter is the
        # only series that can observe it.
        self.budget_pressure_refusals = 0
        self.requests_shed = 0
        self.budget_remaining: dict[str, Decimal] = {}
        self.budget_snapshot_age: float | None = None
        self.budget_reserved: Decimal | None = None
        # A systematic telemetry failure is invisible by construction if the only
        # report is through the machinery that is failing, so it gets a counter
        # of its own alongside the plain-logger record.
        self.telemetry_failures = 0

    def observe_request(self, endpoint: str, status: int) -> None:
        self.requests[(endpoint, status)] += 1

    def observe_query_latency(self, seconds: float) -> None:
        self.latency_count += 1
        self.latency_sum += seconds
        for bound in LATENCY_BUCKETS_SECONDS:
            if seconds <= bound:
                self.latency_buckets[bound] += 1

    def observe_embed_latency(self, seconds: float) -> None:
        """One query-embedding round-trip. Called from SPEC-004's `Retriever`
        through a plain callable, so retrieval never imports the API layer."""
        self.embed_latency_count += 1
        self.embed_latency_sum += seconds
        for bound in LATENCY_BUCKETS_SECONDS:
            if seconds <= bound:
                self.embed_latency_buckets[bound] += 1

    def observe_error(self, code: str) -> None:
        """`spec_for` rather than a bare increment: a code with no registry entry
        has no client-facing rendering, and counting it here while no frontend
        can show it is exactly the half-added failure mode conditions.py exists
        to prevent."""
        spec_for(code)
        self.errors[code] += 1

    def observe_shed(self) -> None:
        self.requests_shed += 1

    def observe_budget_trip(self, ceiling: str) -> None:
        self.budget_trips[ceiling] += 1

    def observe_budget_pressure(self) -> None:
        """Unlabelled, so the series exists from process start at zero.

        `budget_trips_total` carries a `ceiling` label and therefore does not
        exist until something trips, which makes `absent()` on it mean "nothing
        has happened yet" rather than "this replica is not reporting". For a
        refusal that visitors experience, those two must be distinguishable, so
        this one is always emitted. Which ceiling was under pressure is in the
        WARNING record, where it is operator context rather than an alert
        dimension."""
        self.budget_pressure_refusals += 1

    def set_budget_snapshot(self, snapshot: "BudgetSnapshot | None") -> None:
        self.budget_remaining = dict(snapshot.remaining) if snapshot else {}
        self.budget_snapshot_age = snapshot.age_seconds if snapshot else None
        self.budget_reserved = snapshot.reserved if snapshot else None

    def observe_answer(
        self, verdict: str, prompt_tokens: int, completion_tokens: int, cost_usd: Decimal
    ) -> None:
        self.verdicts[verdict] += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cost_usd += cost_usd

    def render(self) -> str:
        lines: list[str] = [
            "# HELP rag_qa_requests_total HTTP requests by endpoint and status.",
            "# TYPE rag_qa_requests_total counter",
        ]
        for (endpoint, status), count in sorted(self.requests.items()):
            lines.append(
                f'rag_qa_requests_total{{endpoint="{_escape(endpoint)}",status="{status}"}} {count}'
            )

        lines += [
            "# HELP rag_qa_query_latency_seconds End-to-end /query latency.",
            "# TYPE rag_qa_query_latency_seconds histogram",
        ]
        for bound in LATENCY_BUCKETS_SECONDS:
            lines.append(
                f'rag_qa_query_latency_seconds_bucket{{le="{bound}"}} {self.latency_buckets[bound]}'
            )
        lines += [
            f'rag_qa_query_latency_seconds_bucket{{le="+Inf"}} {self.latency_count}',
            f"rag_qa_query_latency_seconds_sum {self.latency_sum:.6f}",
            f"rag_qa_query_latency_seconds_count {self.latency_count}",
            "# HELP rag_qa_embed_latency_seconds Query-embedding provider round-trip.",
            "# TYPE rag_qa_embed_latency_seconds histogram",
        ]
        for bound in LATENCY_BUCKETS_SECONDS:
            lines.append(
                f'rag_qa_embed_latency_seconds_bucket{{le="{bound}"}} '
                f"{self.embed_latency_buckets[bound]}"
            )
        lines += [
            f'rag_qa_embed_latency_seconds_bucket{{le="+Inf"}} {self.embed_latency_count}',
            f"rag_qa_embed_latency_seconds_sum {self.embed_latency_sum:.6f}",
            f"rag_qa_embed_latency_seconds_count {self.embed_latency_count}",
            "# HELP rag_qa_verdicts_total Answers by verdict (refusal is a success).",
            "# TYPE rag_qa_verdicts_total counter",
        ]
        for verdict, count in sorted(self.verdicts.items()):
            lines.append(f'rag_qa_verdicts_total{{verdict="{_escape(verdict)}"}} {count}')

        lines += [
            "# HELP rag_qa_prompt_tokens_total Prompt tokens billed.",
            "# TYPE rag_qa_prompt_tokens_total counter",
            f"rag_qa_prompt_tokens_total {self.prompt_tokens}",
            "# HELP rag_qa_completion_tokens_total Completion tokens billed.",
            "# TYPE rag_qa_completion_tokens_total counter",
            f"rag_qa_completion_tokens_total {self.completion_tokens}",
            "# HELP rag_qa_cost_usd_total Generation spend since process start.",
            "# TYPE rag_qa_cost_usd_total counter",
            f"rag_qa_cost_usd_total {self.cost_usd}",
            "# HELP rag_qa_errors_total Error responses by condition code.",
            "# TYPE rag_qa_errors_total counter",
        ]
        for code, count in sorted(self.errors.items()):
            lines.append(f'rag_qa_errors_total{{code="{_escape(code)}"}} {count}')

        lines += [
            "# HELP rag_qa_budget_trips_total Spend-ceiling trips by ceiling.",
            "# TYPE rag_qa_budget_trips_total counter",
        ]
        for ceiling, count in sorted(self.budget_trips.items()):
            lines.append(f'rag_qa_budget_trips_total{{ceiling="{_escape(ceiling)}"}} {count}')

        lines += [
            "# HELP rag_qa_requests_shed_total Requests shed at the concurrency bound.",
            "# TYPE rag_qa_requests_shed_total counter",
            f"rag_qa_requests_shed_total {self.requests_shed}",
            "# HELP rag_qa_budget_pressure_total Requests refused because remaining "
            "headroom was committed to answers in flight.",
            "# TYPE rag_qa_budget_pressure_total counter",
            f"rag_qa_budget_pressure_total {self.budget_pressure_refusals}",
            "# HELP rag_qa_telemetry_failures_total Completion records that could not be emitted.",
            "# TYPE rag_qa_telemetry_failures_total counter",
            f"rag_qa_telemetry_failures_total {self.telemetry_failures}",
        ]

        if self.budget_remaining:
            lines += [
                "# HELP rag_qa_budget_remaining_usd Headroom before the demo stops answering.",
                "# TYPE rag_qa_budget_remaining_usd gauge",
            ]
            for ceiling, amount in sorted(self.budget_remaining.items()):
                lines.append(
                    f'rag_qa_budget_remaining_usd{{ceiling="{_escape(ceiling)}"}} {amount}'
                )

        if self.budget_reserved is not None:
            lines += [
                # Published beside `remaining` rather than folded into it: an
                # alert threshold on remaining keeps the meaning it was written
                # with, and refusals start when this crosses that one.
                "# HELP rag_qa_budget_reserved_usd Headroom committed to answers in flight.",
                "# TYPE rag_qa_budget_reserved_usd gauge",
                f"rag_qa_budget_reserved_usd {self.budget_reserved}",
            ]

        if self.budget_snapshot_age is not None:
            lines += [
                "# HELP rag_qa_budget_snapshot_age_seconds Age of the cached spend totals.",
                "# TYPE rag_qa_budget_snapshot_age_seconds gauge",
                f"rag_qa_budget_snapshot_age_seconds {self.budget_snapshot_age:.3f}",
            ]
        return "\n".join(lines) + "\n"
