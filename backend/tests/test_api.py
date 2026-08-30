"""Contract tests for the typed FastAPI boundary and its safety failures.

The local environment intentionally does not rely on an HTTP client package for
these contract checks.  The tiny adapter below invokes the ASGI app directly,
which keeps the tests offline and exercises FastAPI's real request validation
and exception handlers.
"""

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from signalforge.api.app import create_app
from signalforge.core.config import Settings
from signalforge.core.models import MemoGenerationJob
from signalforge.db.connection import Database
from signalforge.db.repositories import create_memo_job, insert_dataset_version, save_trace
from signalforge.services.local_model import LOCAL_MODEL_UNAVAILABLE, LocalModelError


class OfflineFakeProvider:
    """A provider double proving memo API tests never make model network calls."""

    provider_name = "fake-local"
    model_name = "fake-local-model"

    async def generate_json(
        self,
        _: str,
        __: str,
        response_schema: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        assert response_schema is not None
        raise LocalModelError(LOCAL_MODEL_UNAVAILABLE)


@pytest.fixture
def client(tmp_path) -> "LocalAsgiClient":
    database = Database(tmp_path / "api.duckdb")
    database.apply_schema()
    insert_dataset_version(
        database,
        "v1",
        "fixture",
        "sha256:v1",
        4,
        source_url="https://example.com/source",
        imported_at=(datetime.now(UTC) - timedelta(days=31)).replace(tzinfo=None),
    )
    database.execute(
        """
        INSERT INTO reviews VALUES
            ('r1', 'v1', '服务响应太慢', 1, 'service', 'negative', false),
            ('r2', 'v1', '服务排队时间太长', 1, 'service', 'negative', false),
            ('r3', 'v1', '支付流程清晰', 5, 'app', 'positive', false),
            ('r4', 'v1', '罕见问题只有一条', 3, 'other', 'neutral', false)
        """
    )
    database.execute(
        """
        INSERT INTO market_events VALUES
            ('event-1', 'v1', 'https://example.com/event', '2026-08-01',
             '权威公开事件摘要', 'policy', 'semiconductor', 90)
        """
    )
    app = create_app(
        settings=Settings(
            database_path=str(tmp_path / "unused.duckdb"),
            local_model_base_url=None,
            local_model_name=None,
        ),
        database=database,
        memo_provider=OfflineFakeProvider(),
    )
    yield LocalAsgiClient(app)
    database.close()


class LocalAsgiResponse:
    """The minimal response surface used by these API contract tests."""

    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self._content = content

    def json(self) -> dict[str, Any]:
        return json.loads(self._content.decode("utf-8"))


class LocalAsgiClient:
    """Small synchronous adapter around the ASGI callable, with no network IO."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def get(self, path: str) -> LocalAsgiResponse:
        return self.request("GET", path)

    def post(self, path: str, *, json: dict[str, Any]) -> LocalAsgiResponse:
        return self.request("POST", path, content=json_module_dumps(json))

    def request(self, method: str, target: str, *, content: bytes = b"") -> LocalAsgiResponse:
        return asyncio.run(self._request(method, target, content=content))

    async def _request(
        self, method: str, target: str, *, content: bytes
    ) -> LocalAsgiResponse:
        path, _, query = target.partition("?")
        messages: list[dict[str, Any]] = []
        request_sent = False

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if request_sent:
                return {"type": "http.disconnect"}
            request_sent = True
            return {"type": "http.request", "body": content, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        headers = [(b"content-type", b"application/json")] if content else []
        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await self.app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return LocalAsgiResponse(int(start["status"]), body)


def json_module_dumps(value: dict[str, Any]) -> bytes:
    """Encode request JSON explicitly, avoiding a dependency on an HTTP client."""

    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def test_generate_insight_returns_422_for_insufficient_evidence(client: LocalAsgiClient) -> None:
    response = client.post(
        "/api/v1/insights/generate", json={"dataset_version_id": "v1", "query": "罕见"}
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INSUFFICIENT_EVIDENCE"
    assert response.json()["detail"]["evidence_count"] == 1


def test_trace_api_returns_persisted_model_audit_fields(client: LocalAsgiClient) -> None:
    save_trace(
        client.app.state.database,
        entity_type="memo",
        entity_id="memo-audit-1",
        dataset_version_id="v1",
        prompt_version="memo-v1",
        model_name="qwen-local",
        provider="ollama-local",
        stage="validating_evidence",
        retry_count=2,
        evidence_ids=["r1", "r2"],
        validation_status="accepted",
        latency_ms=17,
        token_estimate=23,
        trace_id="trace-audit-1",
    )

    response = client.get("/api/v1/traces/memo-audit-1")

    assert response.status_code == 200
    trace = response.json()["traces"][0]
    assert trace["id"] == "trace-audit-1"
    assert trace["entity_type"] == "memo"
    assert trace["entity_id"] == "memo-audit-1"
    assert trace["dataset_version_id"] == "v1"
    assert trace["prompt_version"] == "memo-v1"
    assert trace["model_name"] == "qwen-local"
    assert trace["provider"] == "ollama-local"
    assert trace["stage"] == "validating_evidence"
    assert trace["retry_count"] == 2
    assert trace["evidence_ids"] == ["r1", "r2"]
    assert trace["validation_status"] == "accepted"
    assert trace["latency_ms"] == 17
    assert trace["token_estimate"] == 23
    assert trace["created_at"]


def test_overview_health_and_freshness_are_typed(client: LocalAsgiClient) -> None:
    health = client.get("/healthz")
    overview = client.get("/api/v1/overview?dataset_version_id=v1&business_fit=70")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "ready"}
    assert overview.status_code == 200
    body = overview.json()
    assert body["active_dataset"]["id"] == "v1"
    assert body["active_dataset"]["is_stale"] is True
    assert body["summary_metrics"]["review_count"] == 4
    assert body["opportunities"][0]["contributions"]
    assert len(body["risks"]) == 1


def test_model_health_does_not_expose_credentials(client: LocalAsgiClient) -> None:
    response = client.get("/healthz/model")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"configured", "ready", "provider", "model_name", "error_code"}
    assert body == {
        "configured": False,
        "ready": False,
        "provider": "ollama",
        "model_name": None,
        "error_code": "LOCAL_MODEL_UNAVAILABLE",
    }


def test_decision_memo_not_found_uses_a_stable_error_envelope(client: LocalAsgiClient) -> None:
    response = client.get("/api/v1/decision-memo?dataset_version_id=v1")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DECISION_MEMO_NOT_FOUND"


def test_memo_generate_reuses_and_exposes_active_job(client: LocalAsgiClient) -> None:
    timestamp = datetime.now(UTC).replace(tzinfo=None)
    running = MemoGenerationJob(
        id="memo-job-1",
        dataset_version_id="v1",
        status="queued",
        created_at=timestamp,
        updated_at=timestamp,
    )
    create_memo_job(client.app.state.database, running)

    generated = client.post(
        "/api/v1/decision-memo/generate",
        json={"dataset_version_id": "v1"},
    )
    fetched = client.get("/api/v1/decision-memo/jobs/memo-job-1")

    assert generated.status_code == 202
    assert generated.json()["id"] == running.id
    assert fetched.status_code == 200
    assert fetched.json()["dataset_version_id"] == "v1"


def test_decision_memo_job_not_found_uses_a_stable_error_envelope(client: LocalAsgiClient) -> None:
    response = client.get("/api/v1/decision-memo/jobs/missing")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "MEMO_JOB_NOT_FOUND"


def test_model_health_maps_defensively_rejected_url_without_500(
    tmp_path, monkeypatch
) -> None:
    network_calls = 0

    async def reject_network(*args, **kwargs):
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("model health must reject the URL before network access")

    monkeypatch.setattr(httpx.AsyncClient, "request", reject_network)
    database = Database(tmp_path / "invalid-model-url.duckdb")
    settings = Settings(
        database_path=str(tmp_path / "unused.duckdb"),
        local_model_base_url="http://localhost:11434",
        local_model_name="qwen-local:latest",
    )
    # Assignment deliberately simulates a caller bypassing initial settings validation.
    settings.local_model_base_url = "http://user:secret@[::1"
    app = create_app(settings=settings, database=database)

    try:
        response = LocalAsgiClient(app).get("/healthz/model")
    finally:
        database.close()

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "ready": False,
        "provider": "ollama",
        "model_name": "qwen-local:latest",
        "error_code": "LOCAL_MODEL_INVALID_URL",
    }
    assert network_calls == 0


def test_generation_persists_only_evidence_bound_claim_and_trace(client: LocalAsgiClient) -> None:
    response = client.post(
        "/api/v1/insights/generate", json={"dataset_version_id": "v1", "query": "服务"}
    )

    assert response.status_code == 201
    insight = response.json()["insight"]
    assert insight["evidence_ids"] == ["r1", "r2"]
    assert insight["generation_method"] == "fallback"
    traces = client.get(f"/api/v1/traces/{insight['id']}")
    assert traces.status_code == 200
    assert traces.json()["traces"][0]["validation_status"] == "fallback"


def test_evidence_read_is_scoped_to_the_selected_dataset_version(client: LocalAsgiClient) -> None:
    response = client.get("/api/v1/evidence?dataset_version_id=v1&evidence_id=r1")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "r1"
    assert response.json()[0]["dataset_version_id"] == "v1"


def test_invalid_evidence_and_feedback_constraints_do_not_persist(client: LocalAsgiClient) -> None:
    decision = client.post(
        "/api/v1/decisions",
        json={
            "dataset_version_id": "v1",
            "title": "修复服务体验",
            "evidence_ids": ["missing"],
            "problem_statement": "客服响应慢",
            "hypothesis": "增加排队进度提示可以降低投诉",
            "primary_metric": "投诉率",
            "guardrail_metric": "人工客服成本",
            "owner": "产品负责人",
            "due_date": "2026-09-01",
        },
    )
    feedback = client.post(
        "/api/v1/feedback",
        json={"entity_type": "risk", "entity_id": "event-1", "decision": "edited"},
    )

    assert decision.status_code == 422
    assert decision.json()["detail"]["code"] == "INVALID_EVIDENCE"
    assert feedback.status_code == 422
