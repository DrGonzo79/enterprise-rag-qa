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
from collections.abc import Mapping
from decimal import Decimal

from rag_qa.api.conditions import spec_for

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
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = Decimal("0")
        # SPEC-008: the failure signal. A budget trip, a shed, and an embedder
        # mismatch are all `status="503"` on rag_qa_requests_total, so the single
        # most consequential state of this deployment -- the demo has stopped
        # answering -- could not be read from the endpoint an operator reads.
        self.errors: Counter[str] = Counter()
        self.budget_trips: Counter[str] = Counter()
        self.requests_shed = 0
        self.budget_remaining: dict[str, Decimal] = {}

    def observe_request(self, endpoint: str, status: int) -> None:
        self.requests[(endpoint, status)] += 1

    def observe_query_latency(self, seconds: float) -> None:
        self.latency_count += 1
        self.latency_sum += seconds
        for bound in LATENCY_BUCKETS_SECONDS:
            if seconds <= bound:
                self.latency_buckets[bound] += 1

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

    def set_budget_remaining(self, headroom: Mapping[str, Decimal]) -> None:
        self.budget_remaining = dict(headroom)

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
        return "\n".join(lines) + "\n"
