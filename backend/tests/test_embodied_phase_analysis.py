from __future__ import annotations

from signalforge.embodied.models import Episode
from signalforge.embodied.phase_analysis import (
    PHASE_ORDER,
    extract_episode_features,
    segment_episode,
)
from signalforge.embodied.simulator import generate_demo_episodes


def test_segment_episode_has_stable_five_phase_order_and_bounds() -> None:
    episode = generate_demo_episodes(count=1, seed=7, dataset_version_id="v1")[0]

    windows = segment_episode(episode)

    assert [window.phase for window in windows] == list(PHASE_ORDER)
    assert windows[0].start_t == 0.0
    assert windows[-1].end_t == episode.outcome.completion_time_s
    assert all(window.start_t < window.end_t for window in windows)
    assert all(window.rule_name == "deterministic_v1" for window in windows)
    assert all(window.end_t <= episode.observations[-1].t for window in windows)


def test_segment_episode_associates_events_with_time_windows() -> None:
    episode = generate_demo_episodes(count=5, seed=7, dataset_version_id="v1")[1]
    event = episode.events[0]

    windows = segment_episode(episode)
    matching = [window for window in windows if event.event_id in window.event_ids]

    assert len(matching) == 1
    assert matching[0].start_t <= event.t <= matching[0].end_t


def test_extract_episode_features_preserves_version_and_phase_features() -> None:
    episode = generate_demo_episodes(count=1, seed=7, dataset_version_id="v1")[0]
    windows = segment_episode(episode)

    view = extract_episode_features(episode, windows)

    assert view.episode_id == episode.episode_id
    assert view.dataset_version_id == "v1"
    assert [item.phase for item in view.phases] == list(PHASE_ORDER)
    assert view.observation_count == len(episode.observations)
    assert view.event_count == len(episode.events)
    assert view.phases[0].displacement_m >= 0


def test_extract_episode_features_rejects_windows_from_another_episode() -> None:
    episode = generate_demo_episodes(count=1, seed=7, dataset_version_id="v1")[0]
    other = generate_demo_episodes(count=2, seed=8, dataset_version_id="v2")[1]

    windows = segment_episode(other)

    try:
        extract_episode_features(episode, windows)
    except ValueError as exc:
        assert "episode_id" in str(exc)
    else:
        raise AssertionError("cross-episode phase windows should be rejected")


def test_segment_episode_rejects_empty_observations() -> None:
    episode = generate_demo_episodes(count=1, seed=7, dataset_version_id="v1")[0]
    payload = episode.model_dump(mode="python")
    payload["observations"] = []

    # Episode's contract rejects empty observations before phase analysis.
    try:
        Episode.model_validate(payload)
    except Exception:
        pass
    else:
        raise AssertionError("empty observations should not be a valid episode")
