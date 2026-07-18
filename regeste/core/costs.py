"""Rates, calculation and live cost projection during a run (spec §6)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rate:
    """Price per million tokens. Local models = 0/0."""

    input_per_million: float
    output_per_million: float


# Default rates, editable by the user in Settings (spec §6).
# Models absent from the table => cost 0 (matches local backend behavior).
DEFAULT_RATES: dict[str, Rate] = {
    "claude-opus-4-8": Rate(input_per_million=15.0, output_per_million=75.0),
    "claude-sonnet-5": Rate(input_per_million=3.0, output_per_million=15.0),
    "claude-haiku-4-5": Rate(input_per_million=0.80, output_per_million=4.0),
    "gemini-2.5-pro": Rate(input_per_million=1.25, output_per_million=10.0),
    "gemini-2.5-flash": Rate(input_per_million=0.30, output_per_million=2.50),
    "gpt-4o": Rate(input_per_million=2.50, output_per_million=10.0),
}


def compute_cost(tokens_in: int, tokens_out: int, rate: Rate) -> float:
    return (tokens_in / 1_000_000) * rate.input_per_million + (
        tokens_out / 1_000_000
    ) * rate.output_per_million


@dataclass
class Projection:
    """Extrapolation is unstable on the first few files => always shipped with a range."""

    projected_cost: float
    min_cost_per_file: float
    max_cost_per_file: float


@dataclass
class CostTracker:
    """Accumulates costs observed during a run for live display (spec §6)."""

    rates: dict[str, Rate] = field(default_factory=lambda: dict(DEFAULT_RATES))
    observed_costs: list[float] = field(default_factory=list)
    _cumulative_cost: float = field(default=0.0, init=False, repr=False)

    def rate_for(self, model: str) -> Rate:
        return self.rates.get(model, Rate(input_per_million=0.0, output_per_million=0.0))

    def file_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        return compute_cost(tokens_in, tokens_out, self.rate_for(model))

    def record(self, cost: float) -> None:
        self.observed_costs.append(cost)
        self._cumulative_cost += cost

    @property
    def total_cost(self) -> float:
        return self._cumulative_cost

    def project(self, total_files: int) -> Projection | None:
        """Projection = average of files processed so far × total, with observed min/max range."""
        if not self.observed_costs:
            return None
        average = self._cumulative_cost / len(self.observed_costs)
        return Projection(
            projected_cost=average * total_files,
            min_cost_per_file=min(self.observed_costs),
            max_cost_per_file=max(self.observed_costs),
        )


def estimate_before_run(file_count: int, estimated_average_cost: float) -> float:
    """Rough estimate before launch (spec §6): file count × estimated average cost."""
    return file_count * estimated_average_cost
