import pytest

from regeste.core.costs import CostTracker, Rate, compute_cost, estimate_before_run


def test_compute_cost():
    rate = Rate(input_per_million=3.0, output_per_million=15.0)
    cost = compute_cost(tokens_in=1_000_000, tokens_out=1_000_000, rate=rate)
    assert cost == 18.0


def test_compute_cost_local_model_is_free():
    rate = Rate(input_per_million=0.0, output_per_million=0.0)
    assert compute_cost(1_000_000, 1_000_000, rate) == 0.0


def test_cost_tracker_accumulates_total_cost():
    tracker = CostTracker(rates={"m": Rate(input_per_million=1.0, output_per_million=1.0)})
    tracker.record(tracker.file_cost("m", 1_000_000, 0))
    tracker.record(tracker.file_cost("m", 0, 1_000_000))
    assert tracker.total_cost == 2.0


def test_cost_tracker_unknown_model_is_zero():
    tracker = CostTracker(rates={})
    assert tracker.file_cost("unknown-model", 1_000_000, 1_000_000) == 0.0


def test_project_without_data_returns_none():
    tracker = CostTracker(rates={})
    assert tracker.project(10) is None


def test_project_extrapolates_average_with_range():
    tracker = CostTracker(rates={})
    tracker.record(0.10)
    tracker.record(0.20)

    projection = tracker.project(10)

    assert projection.projected_cost == pytest.approx(1.5)  # average (0.15) * 10
    assert projection.min_cost_per_file == 0.10
    assert projection.max_cost_per_file == 0.20


def test_estimate_before_run():
    assert estimate_before_run(file_count=20, estimated_average_cost=0.05) == 1.0
