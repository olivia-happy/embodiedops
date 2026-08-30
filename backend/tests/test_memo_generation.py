"""Two-stage, evidence-safe decision-memo orchestration tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest

from signalforge.db.connection import Database
from signalforge.db.repositories import insert_dataset_version
from signalforge.services.local_model import (
    INVALID_MODEL_JSON,
    LOCAL_MODEL_INVALID_URL,
    LOCAL_MODEL_MODEL_MISMATCH,
    LOCAL_MODEL_NOT_FOUND,
    LOCAL_MODEL_RESPONSE_ERROR,
    LOCAL_MODEL_TIMEOUT,
    LOCAL_MODEL_UNAVAILABLE,
    LocalModelError,
)
from signalforge.services.memo_generation import (
    MemoGenerationError,
    generate_decision_memo,
)
from signalforge.services.memo_validation import (
    grouping_response_schema,
    memo_response_schema,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeProvider:
    provider_name = "fake-local"
    model_name = "fake-local-model"

    def __init__(
        self,
        responses: list[Mapping[str, object] | Exception],
        *,
        delays: list[float] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.delays = list(delays or [0.0] * len(responses))
        self.calls: list[tuple[str, str]] = []
        self.response_schemas: list[Mapping[str, object] | None] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        self.calls.append((system_prompt, user_prompt))
        self.response_schemas.append(response_schema)
        await asyncio.sleep(self.delays.pop(0))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _assert_safe_generation_error(error: BaseException, sentinel: str) -> None:
    pending = [error]
    seen: set[int] = set()
    rendered: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((str(current), repr(current), repr(vars(current))))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)

    assert len(seen) == 1
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sentinel not in "\n".join(rendered)


def test_stage_response_schemas_are_public_and_named() -> None:
    assert grouping_response_schema()["title"] == "GroupingDraft"
    assert memo_response_schema()["title"] == "MemoDraft"


NON_RETRYABLE_LOCAL_MODEL_ERRORS = (
    LOCAL_MODEL_UNAVAILABLE,
    LOCAL_MODEL_TIMEOUT,
    LOCAL_MODEL_RESPONSE_ERROR,
    LOCAL_MODEL_MODEL_MISMATCH,
    LOCAL_MODEL_NOT_FOUND,
    LOCAL_MODEL_INVALID_URL,
)


@pytest.mark.anyio
async def test_unknown_provider_error_does_not_retain_sensitive_exception(
    tmp_path,
) -> None:
    sentinel = "SENSITIVE-PROVIDER-EXCEPTION-MODEL-TEXT"
    db = _database(tmp_path, _service_rows())
    provider = FakeProvider([RuntimeError(sentinel)])

    with pytest.raises(MemoGenerationError) as captured:
        await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert captured.value.code == "MODEL_PROVIDER_ERROR"
    _assert_safe_generation_error(captured.value, sentinel)


@pytest.mark.anyio
async def test_terminal_memo_schema_error_does_not_retain_model_payload(
    tmp_path,
) -> None:
    sentinel = "SENSITIVE-INVALID-MEMO-PAYLOAD"
    db = _database(tmp_path, _service_rows())
    invalid_draft = {"decision_statement": sentinel}
    provider = FakeProvider(
        [
            _grouping(
                _subproblem("排队透明度", ["r1", "r2"], counter_ids=["r4"])
            ),
            invalid_draft,
            invalid_draft,
        ]
    )

    with pytest.raises(MemoGenerationError) as captured:
        await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert captured.value.code == "INVALID_MEMO_SCHEMA"
    _assert_safe_generation_error(captured.value, sentinel)


def _database(tmp_path, rows: list[tuple[object, ...]]) -> Database:
    db = Database(tmp_path / "memo-generation.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "fixture", "sha256:v1", len(rows))
    for row in rows:
        db.execute("INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)", row)
    return db


def _service_rows() -> list[tuple[object, ...]]:
    return [
        ("r1", "v1", "午高峰看不到排队进度。", 1, "service", "negative", False),
        ("r2", "v1", "等待时没有预计完成时间。", 2, "service", "negative", False),
        ("r3", "v1", "客服首次响应太慢。", 3, "service", "negative", False),
        ("r4", "v1", "订单页的进度提示很清楚。", 2, "service", "positive", False),
    ]


def _subproblem(
    name: str,
    supporting_ids: list[str],
    *,
    counter_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "mechanism": f"{name}会增加服务过程的不确定性。",
        "supporting_evidence_ids": supporting_ids,
        "counter_evidence_ids": counter_ids or [],
        "missing_information": ["午高峰等待时长", "客服首次响应时长"],
    }


def _grouping(*subproblems: Mapping[str, object]) -> dict[str, object]:
    return {
        "subproblems": list(subproblems),
        "counter_evidence_checked": True,
    }


def _actionable_draft() -> dict[str, object]:
    return {
        "decision_status": "needs_evidence",
        "decision_statement": "先在午高峰验证排队进度展示。",
        "topic": "service",
        "subproblem": "排队透明度",
        "facts": {"review_count": 999, "negative_rate": 1.0},
        "supporting_evidence": [
            {"evidence_id": "r1", "rationale": "缺少排队进度。"},
            {"evidence_id": "r2", "rationale": "缺少预计完成时间。"},
        ],
        "counter_evidence": [
            {"evidence_id": "r4", "rationale": "部分订单已有清晰进度。"}
        ],
        "unknowns": ["展示进度是否会降低订单查询率"],
        "reasoning_summary": "多条反馈支持同一机制，且存在相反体验。",
        "experiment": {
            "hypothesis": "展示排队位置和预计完成时间会减少订单进度查询。",
            "target_segment": "午高峰顾客",
            "intervention": "展示排队位置与预计完成时间。",
            "primary_metric": "订单进度查询率",
            "guardrail_metric": "订单取消率",
        },
    }


@pytest.mark.anyio
async def test_current_demo_returns_needs_evidence_without_second_model_call(
    tmp_path,
) -> None:
    db = _database(tmp_path, _service_rows())
    provider = FakeProvider(
        [
            _grouping(
                _subproblem("排队透明度", ["r1"], counter_ids=["r4"]),
                _subproblem("客服响应时效", ["r3"]),
            )
        ]
    )
    stages: list[str] = []

    memo = await generate_decision_memo(db, "v1", provider, stages.append)

    assert memo.decision_status == "needs_evidence"
    assert memo.experiment is None
    assert memo.evidence_plan is not None
    assert memo.evidence_plan.minimum_evidence_per_subproblem == 3
    assert provider.call_count == 1
    assert stages == ["analyzing_signals", "grouping_evidence", "validating_evidence"]


@pytest.mark.anyio
async def test_actionable_case_calls_memo_stage_and_injects_db_numbers(
    tmp_path,
) -> None:
    db = _database(tmp_path, _service_rows())
    provider = FakeProvider(
        [
            _grouping(
                _subproblem("排队透明度", ["r1", "r2"], counter_ids=["r4"])
            ),
            _actionable_draft(),
        ]
    )
    stages: list[str] = []

    memo = await generate_decision_memo(db, "v1", provider, stages.append)

    assert memo.decision_status == "actionable"
    assert memo.facts.review_count == 4
    assert memo.facts.negative_count == 3
    assert memo.facts.negative_rate == 75.0
    assert memo.facts.average_rating == 2.0
    assert memo.facts.severity == 75.0
    assert memo.facts.affected == 100.0
    assert memo.facts.evidence == 40.0
    assert memo.facts.opportunity_score == 78.3
    assert memo.counter_evidence_checked is True
    assert memo.experiment is not None
    assert memo.experiment.duration_days == 14
    assert memo.experiment.stop_conditions == ["若护栏指标恶化则停止实验"]
    assert provider.call_count == 2
    assert [
        schema.get("title") if schema is not None else None
        for schema in provider.response_schemas
    ] == ["GroupingDraft", "MemoDraft"]
    assert stages[-1] == "generating_memo"
    statuses = [
        row[0]
        for row in db.execute(
            "SELECT validation_status FROM traces ORDER BY created_at, id"
        ).fetchall()
    ]
    assert "numeric_mismatch" in statuses
    assert "accepted" in statuses
    audit_rows = db.execute(
        """
        SELECT stage, provider, retry_count, latency_ms, validation_status,
               token_estimate
        FROM traces ORDER BY created_at, id
        """
    ).fetchall()
    assert any(
        row[0:3] == ("grouping_evidence", "fake-local", 0)
        and row[4] == "accepted"
        for row in audit_rows
    )
    assert any(
        row[0:3] == ("generating_memo", "fake-local", 0)
        and row[4] == "accepted"
        for row in audit_rows
    )
    assert any(
        row[0:3] == ("analyzing_signals", "fake-local", 0)
        and row[4] == "accepted"
        for row in audit_rows
    )
    validation_rows = [
        row for row in audit_rows if row[0] == "validating_evidence"
    ]
    assert {row[4] for row in validation_rows} >= {"accepted", "numeric_mismatch"}
    assert all(row[5] == 0 for row in validation_rows)
    model_call_rows = [
        row
        for row in audit_rows
        if row[0] in {"grouping_evidence", "generating_memo"}
    ]
    assert len(model_call_rows) == 2
    assert all(row[5] > 0 for row in model_call_rows)


@pytest.mark.anyio
async def test_grouping_prompt_is_version_scoped_redaction_safe_and_bounded(
    tmp_path,
) -> None:
    long_content = "可见证据" + "甲" * 700 + "截断后不可见"
    rows = [
        ("visible", "v1", long_content, 1, "service", "negative", False),
        ("secret", "v1", "脱敏正文绝不能进入提示词", 1, "service", "negative", True),
        ("visible-2", "v1", "客服响应慢", 2, "service", "negative", False),
    ]
    db = _database(tmp_path, rows)
    insert_dataset_version(db, "v2", "fixture", "sha256:v2", 1)
    db.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("foreign", "v2", "其他版本正文", 1, "service", "negative", False),
    )
    provider = FakeProvider(
        [_grouping(_subproblem("排队透明度", ["visible"]))]
    )

    await generate_decision_memo(db, "v1", provider, lambda _: None)

    system_prompt, user_prompt = provider.calls[0]
    assert "JSON only" in system_prompt
    assert "step by step" not in system_prompt.lower()
    assert "可见证据" in user_prompt
    assert "截断后不可见" not in user_prompt
    assert "脱敏正文绝不能进入提示词" not in user_prompt
    assert "其他版本正文" not in user_prompt


@pytest.mark.anyio
async def test_rejected_grouping_is_a_failed_validation_not_a_saved_refusal(
    tmp_path,
) -> None:
    db = _database(tmp_path, _service_rows())
    provider = FakeProvider(
        [_grouping(_subproblem("排队透明度", ["r1", "made-up-id"]))]
    )

    with pytest.raises(MemoGenerationError) as captured:
        await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert captured.value.code == "UNKNOWN_EVIDENCE_ID"
    assert provider.call_count == 1
    assert db.execute("SELECT COUNT(*) FROM decision_memos").fetchone() == (0,)
    assert db.execute(
        """
        SELECT validation_status FROM traces
        WHERE stage = 'validating_evidence' AND validation_status = 'invalid'
        """
    ).fetchone() == ("invalid",)


@pytest.mark.anyio
async def test_invalid_model_json_retries_once_then_succeeds(tmp_path) -> None:
    db = _database(tmp_path, _service_rows())
    provider = FakeProvider(
        [
            LocalModelError(INVALID_MODEL_JSON),
                _grouping(
                    _subproblem("排队透明度", ["r1"], counter_ids=["r4"]),
                    _subproblem("客服响应时效", ["r3"]),
            ),
        ]
    )

    memo = await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert memo.decision_status == "needs_evidence"
    assert provider.call_count == 2
    statuses = {
        row[0] for row in db.execute("SELECT validation_status FROM traces").fetchall()
    }
    assert statuses >= {"retried", "accepted"}
    retry_audit = db.execute(
        """
        SELECT provider, stage, retry_count
        FROM traces WHERE validation_status = 'retried'
        """
    ).fetchone()
    assert retry_audit == ("fake-local", "grouping_evidence", 1)
    accepted_audit = db.execute(
        """
        SELECT provider, stage, retry_count
        FROM traces
        WHERE validation_status = 'accepted' AND stage = 'grouping_evidence'
        """
    ).fetchone()
    assert accepted_audit == ("fake-local", "grouping_evidence", 1)


@pytest.mark.anyio
async def test_invalid_model_json_retries_memo_draft_once_then_succeeds(
    tmp_path,
) -> None:
    db = _database(tmp_path, _service_rows())
    provider = FakeProvider(
        [
            _grouping(
                _subproblem("排队透明度", ["r1", "r2"], counter_ids=["r4"])
            ),
            LocalModelError(INVALID_MODEL_JSON),
            _actionable_draft(),
        ]
    )

    memo = await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert memo.decision_status == "actionable"
    assert provider.call_count == 3
    assert db.execute(
        """
        SELECT validation_status, retry_count
        FROM traces WHERE stage = 'generating_memo'
        ORDER BY created_at, id
        """
    ).fetchall() == [("retried", 1), ("accepted", 1)]


@pytest.mark.anyio
@pytest.mark.parametrize("error_code", NON_RETRYABLE_LOCAL_MODEL_ERRORS)
async def test_infrastructure_model_error_fails_grouping_without_retry(
    tmp_path,
    error_code: str,
) -> None:
    db = _database(tmp_path, _service_rows())
    provider = FakeProvider([LocalModelError(error_code)])

    with pytest.raises(LocalModelError) as captured:
        await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert captured.value.code == error_code
    assert provider.call_count == 1
    assert db.execute(
        """
        SELECT validation_status, retry_count
        FROM traces WHERE stage = 'grouping_evidence'
        """
    ).fetchall() == [("failed", 0)]
    assert db.execute(
        "SELECT COUNT(*) FROM traces WHERE validation_status = 'retried'"
    ).fetchone() == (0,)


@pytest.mark.anyio
@pytest.mark.parametrize("error_code", NON_RETRYABLE_LOCAL_MODEL_ERRORS)
async def test_infrastructure_model_error_fails_memo_draft_without_retry(
    tmp_path,
    error_code: str,
) -> None:
    db = _database(tmp_path, _service_rows())
    provider = FakeProvider(
        [
            _grouping(
                _subproblem("排队透明度", ["r1", "r2"], counter_ids=["r4"])
            ),
            LocalModelError(error_code),
        ]
    )

    with pytest.raises(LocalModelError) as captured:
        await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert captured.value.code == error_code
    assert provider.call_count == 2
    assert db.execute(
        """
        SELECT validation_status, retry_count
        FROM traces WHERE stage = 'generating_memo'
        """
    ).fetchall() == [("failed", 0)]
    assert db.execute(
        "SELECT COUNT(*) FROM traces WHERE validation_status = 'retried'"
    ).fetchone() == (0,)


@pytest.mark.anyio
async def test_invalid_grouping_schema_retries_once_then_fails_without_memo(
    tmp_path,
) -> None:
    db = _database(tmp_path, _service_rows())
    invalid = {"subproblems": []}
    provider = FakeProvider([invalid, invalid])

    with pytest.raises(MemoGenerationError) as captured:
        await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert captured.value.code == "INVALID_GROUPING_SCHEMA"
    assert provider.call_count == 2
    assert db.execute("SELECT COUNT(*) FROM decision_memos").fetchone() == (0,)
    statuses = [
        row[0]
        for row in db.execute(
            """
            SELECT validation_status FROM traces
            WHERE stage = 'validating_evidence'
            ORDER BY created_at, id
            """
        ).fetchall()
    ]
    assert statuses == ["retried", "invalid"]


@pytest.mark.anyio
async def test_small_but_complete_topic_returns_explicit_refusal(tmp_path) -> None:
    rows = _service_rows()[:2]
    db = _database(tmp_path, rows)
    provider = FakeProvider(
        [_grouping(_subproblem("排队透明度", ["r1", "r2"]))]
    )

    memo = await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert memo.decision_status == "refused"
    assert memo.refusal_reason == "INSUFFICIENT_TOPIC_EVIDENCE"
    assert memo.experiment is None
    assert memo.evidence_plan is None
    assert provider.call_count == 1
    statuses = [
        row[0] for row in db.execute("SELECT validation_status FROM traces").fetchall()
    ]
    assert "refused" in statuses


@pytest.mark.anyio
async def test_highest_scoring_complete_topic_is_selected(tmp_path) -> None:
    rows = [
        ("s1", "v1", "服务慢", 1, "service", "negative", False),
        ("s2", "v1", "服务响应慢", 1, "service", "negative", False),
        ("s3", "v1", "等待服务", 2, "service", "negative", False),
        ("d1", "v1", "配送很好", 5, "delivery", "positive", False),
        ("f1", "v1", "餐品信息缺失", None, "food", "unknown", False),
    ]
    db = _database(tmp_path, rows)
    provider = FakeProvider(
        [_grouping(_subproblem("响应时效", ["s1"]))]
    )

    memo = await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert memo.topic == "service"
    assert memo.decision_status == "needs_evidence"
    assert '"topic":"service"' in provider.calls[0][1]


@pytest.mark.anyio
async def test_unauthorized_numeric_memo_text_retries_once_then_fails(
    tmp_path,
) -> None:
    db = _database(tmp_path, _service_rows())
    grouping = _grouping(
        _subproblem("排队透明度", ["r1", "r2"], counter_ids=["r4"])
    )
    numeric_draft = _actionable_draft()
    numeric_draft["reasoning_summary"] = "模型声称成功率会达到 93%。"
    provider = FakeProvider([grouping, numeric_draft, numeric_draft])

    with pytest.raises(MemoGenerationError) as captured:
        await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert captured.value.code == "NUMERIC_CLAIM_IN_MODEL_TEXT"
    assert provider.call_count == 3
    assert db.execute("SELECT COUNT(*) FROM decision_memos").fetchone() == (0,)
    memo_attempts = db.execute(
        """
        SELECT validation_status, provider, stage, retry_count, token_estimate
        FROM traces
        WHERE stage = 'validating_evidence'
          AND validation_status IN ('retried', 'invalid')
        ORDER BY created_at, id
        """
    ).fetchall()
    assert {row[0] for row in memo_attempts} == {"retried", "invalid"}
    assert all(row[1] == "fake-local" for row in memo_attempts)
    assert all(row[2] == "validating_evidence" for row in memo_attempts)
    assert all(row[3] == 1 for row in memo_attempts)
    assert all(row[4] == 0 for row in memo_attempts)
    generated_calls = db.execute(
        """
        SELECT token_estimate FROM traces
        WHERE stage = 'generating_memo' AND validation_status = 'accepted'
        """
    ).fetchall()
    assert len(generated_calls) == 2
    assert all(row[0] > 0 for row in generated_calls)


@pytest.mark.anyio
async def test_trace_latency_is_scoped_to_each_model_stage(tmp_path) -> None:
    db = _database(tmp_path, _service_rows())
    provider = FakeProvider(
        [
            _grouping(
                _subproblem("排队透明度", ["r1", "r2"], counter_ids=["r4"])
            ),
            _actionable_draft(),
        ],
        delays=[0.06, 0.0],
    )

    await generate_decision_memo(db, "v1", provider, lambda _: None)

    grouped = db.execute(
        """
        SELECT latency_ms FROM traces
        WHERE stage = 'grouping_evidence' AND validation_status = 'accepted'
        """
    ).fetchone()[0]
    drafted = db.execute(
        """
        SELECT latency_ms FROM traces
        WHERE stage = 'generating_memo' AND validation_status = 'accepted'
        """
    ).fetchone()[0]
    assert grouped >= 40
    assert drafted < grouped


@pytest.mark.anyio
async def test_grouping_evidence_cap_keeps_support_and_counter_buckets(
    tmp_path,
) -> None:
    rows = [
        (
            f"negative-{index:02d}",
            "v1",
            f"负向服务反馈 {index}",
            1,
            "service",
            "negative",
            False,
        )
        for index in range(50)
    ]
    rows.extend(
        [
            (
                f"counter-{index:02d}",
                "v1",
                f"服务体验清晰 {index}",
                5,
                "service",
                "positive",
                False,
            )
            for index in range(3)
        ]
    )
    db = _database(tmp_path, rows)
    provider = FakeProvider(
        [
            _grouping(
                _subproblem(
                    "服务透明度",
                    ["negative-00"],
                    counter_ids=["counter-00", "counter-01", "counter-02"],
                )
            )
        ]
    )

    memo = await generate_decision_memo(db, "v1", provider, lambda _: None)

    prompt = json.loads(provider.calls[0][1])
    selected = prompt["allowed_evidence"]
    selected_ids = {item["evidence_id"] for item in selected}
    assert len(selected) == 40
    assert {"counter-00", "counter-01", "counter-02"} <= selected_ids
    assert any(evidence_id.startswith("negative-") for evidence_id in selected_ids)
    assert memo.decision_status == "needs_evidence"


@pytest.mark.anyio
async def test_prompt_subset_cannot_claim_complete_counter_evidence(
    tmp_path,
) -> None:
    rows = [
        (
            f"negative-{index:02d}",
            "v1",
            f"Negative service evidence {index}",
            1,
            "service",
            "negative",
            False,
        )
        for index in range(2)
    ]
    rows.extend(
        [
            (
                f"positive-{index:02d}",
                "v1",
                f"Positive service counter evidence {index}",
                5,
                "service",
                "positive",
                False,
            )
            for index in range(50)
        ]
    )
    db = _database(tmp_path, rows)
    provider = FakeProvider(
        [
            _grouping(
                _subproblem(
                    "service transparency",
                    ["negative-00", "negative-01"],
                    counter_ids=[f"positive-{index:02d}" for index in range(38)],
                )
            )
        ]
    )

    memo = await generate_decision_memo(db, "v1", provider, lambda _: None)

    prompt_ids = {
        item["evidence_id"]
        for item in json.loads(provider.calls[0][1])["allowed_evidence"]
    }
    assert len(prompt_ids) == 40
    assert "positive-49" not in prompt_ids
    assert memo.decision_status == "refused"
    assert memo.counter_evidence_checked is False
    assert memo.refusal_reason == "COUNTER_EVIDENCE_NOT_CHECKED"
    assert provider.call_count == 1


@pytest.mark.anyio
async def test_all_positive_topic_cannot_become_actionable(tmp_path) -> None:
    rows = [
        ("p1", "v1", "服务清晰", 5, "service", "positive", False),
        ("p2", "v1", "响应及时", 5, "service", "positive", False),
        ("p3", "v1", "体验稳定", 5, "service", "positive", False),
    ]
    db = _database(tmp_path, rows)
    provider = FakeProvider(
        [
            _grouping(
                _subproblem("伪问题一", ["p1"], counter_ids=["p2", "p3"]),
                _subproblem("伪问题二", ["p2"], counter_ids=["p1"]),
            )
        ]
    )

    memo = await generate_decision_memo(db, "v1", provider, lambda _: None)

    assert memo.decision_status == "refused"
    assert memo.experiment is None
    assert provider.call_count == 1
