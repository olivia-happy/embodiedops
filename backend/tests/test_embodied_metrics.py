from __future__ import annotations

from signalforge.embodied.metrics import aggregate_task_metrics, failure_type_distribution
from signalforge.embodied.simulator import generate_demo_episodes


def test_aggregate_task_metrics_keeps_explicit_denominators() -> None:
    episodes = generate_demo_episodes(count=6, seed=4, dataset_version_id="v1")

    metrics = aggregate_task_metrics(episodes)

    assert metrics.episode_count == 6
    assert metrics.success_count == 1
    assert metrics.success_rate.numerator == 1
    assert metrics.success_rate.denominator == 6
    assert metrics.success_rate.rate == 1 / 6
    assert metrics.grasp_success_rate.denominator == 6
    assert metrics.placement_success_rate.denominator == 6
    assert metrics.collision_rate.numerator == 1
    assert metrics.timeout_rate.numerator == 1
    assert metrics.mean_completion_time_s == 4.0
    assert any(item.failure_count > 0 for item in metrics.per_phase_failure_rate)


def test_failure_type_distribution_is_sorted_and_includes_success() -> None:
    episodes = generate_demo_episodes(count=6, seed=4, dataset_version_id="v1")

    distribution = failure_type_distribution(episodes)

    assert [item.failure_type for item in distribution] == sorted(
        item.failure_type for item in distribution
    )
    assert {item.failure_type for item in distribution} >= {
        "success",
        "grasp_miss",
        "occlusion",
        "collision",
        "planner_unreachable",
        "timeout",
    }
    assert sum(item.count for item in distribution) == len(episodes)


def test_empty_metrics_use_none_for_undefined_rates() -> None:
    metrics = aggregate_task_metrics([])

    assert metrics.episode_count == 0
    assert metrics.success_rate.numerator == 0
    assert metrics.success_rate.denominator == 0
    assert metrics.success_rate.rate is None
    assert metrics.mean_completion_time_s is None
    assert metrics.per_phase_failure_rate

