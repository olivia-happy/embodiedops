"""Offline lifecycle tests for persisted decision-memo jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from signalforge.core.models import MemoGenerationJob
from signalforge.db.connection import Database
from signalforge.db.repositories import (
    create_memo_job,
    get_decision_memo,
    get_memo_job,
    insert_dataset_version,
)
from signalforge.services.local_model import LOCAL_MODEL_UNAVAILABLE, LocalModelError
from signalforge.services.memo_jobs import MemoJobManager


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeProvider:
    provider_name = "fake-local"
    model_name = "fake-local-model"

    def __init__(self, responses: list[Mapping[str, object] | Exception]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def generate_json(
        self,
        _: str,
        __: str,
        response_schema: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        assert response_schema is not None
        self.calls += 1
        await asyncio.sleep(0)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _database(tmp_path) -> Database:
    db = Database(tmp_path / "memo-jobs.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "fixture", "sha256:v1", 4)
    for row in (
        ("r1", "v1", "排队进度不可见", 1, "service", "negative", False),
        ("r2", "v1", "没有预计完成时间", 2, "service", "negative", False),
        ("r3", "v1", "客服首次响应太慢", 3, "service", "negative", False),
        ("r4", "v1", "订单进度提示清晰", 2, "service", "positive", False),
    ):
        db.execute("INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)", row)
    return db


def _job() -> MemoGenerationJob:
    from datetime import datetime

    timestamp = datetime(2026, 8, 10, 9, 0)
    return MemoGenerationJob(
        id="job-1",
        dataset_version_id="v1",
        status="queued",
        created_at=timestamp,
        updated_at=timestamp,
    )


def _needs_evidence_grouping() -> dict[str, object]:
    return {
        "subproblems": [
            {
                "name": "排队透明度",
                "mechanism": "用户无法预估取餐进度",
                "supporting_evidence_ids": ["r1"],
                "counter_evidence_ids": ["r4"],
                "missing_information": ["午高峰等待时长"],
            },
            {
                "name": "客服响应时效",
                "mechanism": "首次响应存在延迟",
                "supporting_evidence_ids": ["r3"],
                "counter_evidence_ids": [],
                "missing_information": ["首次响应时长"],
            },
        ],
        "counter_evidence_checked": True,
    }


@pytest.mark.anyio
async def test_job_records_real_stages_and_atomically_persists_a_memo(tmp_path) -> None:
    db = _database(tmp_path)
    create_memo_job(db, _job())
    manager = MemoJobManager(db, FakeProvider([_needs_evidence_grouping()]))

    await manager.run_job("job-1")

    job = get_memo_job(db, "job-1")
    memo = get_decision_memo(db, "v1")
    assert job is not None and job.status == "completed"
    assert memo is not None and memo.decision_status == "needs_evidence"


@pytest.mark.anyio
async def test_model_failure_marks_job_failed_without_saving_a_memo(tmp_path) -> None:
    db = _database(tmp_path)
    create_memo_job(db, _job())
    manager = MemoJobManager(db, FakeProvider([LocalModelError(LOCAL_MODEL_UNAVAILABLE)]))

    await manager.run_job("job-1")

    job = get_memo_job(db, "job-1")
    assert job is not None and job.status == "failed"
    assert job.error_code == LOCAL_MODEL_UNAVAILABLE
    assert get_decision_memo(db, "v1") is None


@pytest.mark.anyio
async def test_start_is_idempotent_for_one_job_id(tmp_path) -> None:
    db = _database(tmp_path)
    create_memo_job(db, _job())
    provider = FakeProvider([_needs_evidence_grouping()])
    manager = MemoJobManager(db, provider)

    manager.start("job-1")
    manager.start("job-1")
    await asyncio.sleep(0.05)
    await manager.close()

    assert provider.calls == 1
