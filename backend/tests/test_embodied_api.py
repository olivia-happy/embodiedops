"""FastAPI contracts for the read-only embodied diagnosis surface."""

import asyncio
import json
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI

from signalforge.api.app import create_app
from signalforge.core.config import Settings
from signalforge.db.connection import Database
from signalforge.db.repositories import insert_dataset_version
from signalforge.embodied.simulator import generate_demo_episodes


class _Response:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return json.loads(self._body.decode())


class _Client:
    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> _Response:
        return asyncio.run(self._request(method, path, body))

    async def _request(self, method: str, target: str, body: dict[str, Any] | None) -> _Response:
        path, _, query = target.partition("?")
        messages: list[dict[str, Any]] = []
        payload = json.dumps(body or {}).encode()
        sent = False

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await self.app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": query.encode(),
                "headers": [(b"content-type", b"application/json")],
                "client": ("test", 1),
                "server": ("test", 80),
            },
            receive,
            send,
        )
        start = next(item for item in messages if item["type"] == "http.response.start")
        body_bytes = b"".join(
            item.get("body", b"") for item in messages if item["type"] == "http.response.body"
        )
        return _Response(int(start["status"]), body_bytes)

    def get(self, path: str) -> _Response:
        return self.request("GET", path)

    def post(self, path: str, body: dict[str, Any]) -> _Response:
        return self.request("POST", path, body)


@pytest.fixture
def embodied_client(tmp_path):
    db = Database(tmp_path / "api.duckdb")
    db.apply_schema()
    insert_dataset_version(
        db, "emb-v1", "synthetic", "sha256:emb-v1", 3, imported_at=datetime(2026, 8, 11, 9, 0)
    )
    episodes = generate_demo_episodes(count=3, seed=7, dataset_version_id="emb-v1")
    from signalforge.db.repositories import insert_embodied_episodes

    insert_embodied_episodes(db, episodes)
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "unused.duckdb"), demo_read_only=True),
        database=db,
    )
    yield _Client(app)
    db.close()


def test_embodied_tasks_and_episode_return_versioned_metrics(embodied_client: _Client) -> None:
    tasks = embodied_client.get("/api/v1/embodied/tasks?dataset_version_id=emb-v1")
    assert tasks.status_code == 200
    assert tasks.json()["dataset_version_id"] == "emb-v1"
    assert tasks.json()["tasks"][0]["metrics"]["success_rate"]["denominator"] == 3

    episode = embodied_client.get(
        "/api/v1/embodied/episodes/embodied-demo-0001?dataset_version_id=emb-v1"
    )
    assert episode.status_code == 200
    assert [phase["phase"] for phase in episode.json()["phases"]] == [
        "approach",
        "align",
        "grasp",
        "transfer",
        "place",
    ]


def test_embodied_writes_are_rejected_in_demo_read_only_mode(embodied_client: _Client) -> None:
    response = embodied_client.post(
        "/api/v1/embodied/diagnoses",
        {"dataset_version_id": "emb-v1", "episode_id": "embodied-demo-0001"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEMO_READ_ONLY"


def test_embodied_local_import_accepts_jsonl_and_is_idempotent(embodied_client: _Client) -> None:
    embodied_client.app.state.settings.demo_read_only = False
    episode = generate_demo_episodes(count=1, seed=99, dataset_version_id="emb-v2")[0]
    body = {
        "dataset_version_id": "emb-v2",
        "manifest": {"source": "synthetic_tabletop_fixture", "real_robot_data": False},
        "jsonl": json.dumps(episode.model_dump(mode="json"), ensure_ascii=False),
    }
    first = embodied_client.post("/api/v1/embodied/episodes/import", body)
    second = embodied_client.post("/api/v1/embodied/episodes/import", body)
    assert first.status_code == 201
    assert first.json()["imported_count"] == 1
    assert second.status_code == 201
    assert second.json()["imported_count"] == 0


def test_embodied_unknown_episode_and_cross_version_are_not_leaked(
    embodied_client: _Client,
) -> None:
    missing = embodied_client.get("/api/v1/embodied/episodes/nope?dataset_version_id=emb-v1")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "EPISODE_NOT_FOUND"
    foreign = embodied_client.get(
        "/api/v1/embodied/episodes/embodied-demo-0001?dataset_version_id=other"
    )
    assert foreign.status_code == 404


def test_embodied_dataset_catalog_keeps_failure_and_success_baselines_separate(
    embodied_client: _Client,
) -> None:
    response = embodied_client.get("/api/v1/embodied/datasets")

    assert response.status_code == 200
    dataset = response.json()["datasets"][0]
    assert dataset["dataset_version_id"] == "emb-v1"
    assert dataset["purpose"] == "failure_diagnosis_regression"
    assert dataset["real_robot_data"] is False
    assert dataset["default_episode_id"] == "embodied-demo-0001"
