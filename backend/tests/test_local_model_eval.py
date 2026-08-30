"""Offline contracts for the opt-in local-model evaluation.

Every provider in this module is a deterministic fake.  These tests must never
start Ollama or make a network request.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest
from eval.local_model_metrics import (
    SystemRunAssessment,
    audit_grouping_payload,
    audit_memo_payload,
    ratio,
    summarize_attempts,
    summarize_system_runs,
)
from eval.run_local_model_eval import (
    RecordingProvider,
    evaluate_local_model,
    render_markdown_report,
)

from signalforge.services.local_model import (
    LOCAL_MODEL_MODEL_MISMATCH,
    LOCAL_MODEL_RESPONSE_ERROR,
    LOCAL_MODEL_UNAVAILABLE,
    LocalModelError,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeMetadataProvider:
    """Sequence provider that exposes only sanitized call metadata to tests."""

    provider_name = "ollama"
    model_name = "qwen3.5:9b"

    def __init__(self, outcomes: list[Mapping[str, object] | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.response_models: list[str | None] = []
        self.metadata_snapshots: list[SimpleNamespace] = []
        self.metadata_call_count = 0
        self.schema_titles: list[str | None] = []
        self.dataset_version_ids: list[str] = []
        self.settings = SimpleNamespace(
            local_model_base_url="http://localhost:11434",
            local_model_name=self.model_name,
            local_model_context_length=16384,
            local_model_temperature=0.0,
            local_model_seed=42,
            local_model_max_output_tokens=2048,
            local_model_think=True,
            local_model_timeout_seconds=120.0,
        )

    async def generate_json_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, object] | None = None,
    ) -> SimpleNamespace:
        del system_prompt
        title = response_schema.get("title") if response_schema is not None else None
        self.schema_titles.append(str(title) if title is not None else None)
        request_data = json.loads(user_prompt)
        self.dataset_version_ids.append(str(request_data["dataset_version_id"]))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        response_model = (
            self.response_models.pop(0)
            if self.response_models
            else self.model_name
        )
        return SimpleNamespace(
            payload=outcome,
            usage=SimpleNamespace(
                prompt_eval_count=120,
                eval_count=48,
                total_duration_ns=2_000_000,
                load_duration_ns=500_000,
                prompt_eval_duration_ns=600_000,
                eval_duration_ns=700_000,
            ),
            response_model=response_model,
        )

    async def model_metadata(self) -> SimpleNamespace:
        self.metadata_call_count += 1
        if self.metadata_snapshots:
            return self.metadata_snapshots.pop(0)
        return _metadata_snapshot()


def _metadata_snapshot(
    *,
    tag: str = "qwen3.5:9b",
    digest: str = "sha256:fixture-qwen35-9b",
) -> SimpleNamespace:
    return SimpleNamespace(
            tag=tag,
            digest=digest,
            size_bytes=6_600_000_000,
            parameter_size="9B",
            quantization="Q4_K_M",
            format="gguf",
            family="qwen3",
            license_id="Apache-2.0",
            ollama_version="0.32.6",
            loaded_size_vram=6_500_000_000,
            loaded_context_length=16384,
    )


def _assert_safe_exception(error: BaseException, sentinel: str) -> None:
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


def _subproblem(
    name: str,
    supporting_ids: list[str],
    counter_ids: list[str],
) -> dict[str, object]:
    return {
        "name": name,
        "mechanism": "因为服务进度反馈环节缺少清晰状态，导致顾客无法判断等待路径。",
        "supporting_evidence_ids": supporting_ids,
        "counter_evidence_ids": counter_ids,
        "missing_information": ["需要补充时段与渠道字段"],
    }


def _grouping(*subproblems: Mapping[str, object]) -> dict[str, object]:
    return {
        "subproblems": list(subproblems),
        "counter_evidence_checked": True,
    }


def _actionable_draft() -> dict[str, object]:
    return {
        "decision_statement": "验证排队进度展示能否减少重复咨询。",
        "topic": "service",
        "subproblem": "排队透明度",
        "supporting_evidence": [
            {"evidence_id": "c1-n1", "rationale": "顾客无法获知排队位置。"},
            {"evidence_id": "c1-n2", "rationale": "顾客无法安排后续时间。"},
            {"evidence_id": "c1-n3", "rationale": "阶段反馈缺失会触发重复咨询。"},
        ],
        "counter_evidence": [
            {"evidence_id": "c1-p1", "rationale": "已有门店的提示体验更顺畅。"}
        ],
        "unknowns": ["不同门店和时段的效果是否一致"],
        "reasoning_summary": "支持证据指向同一反馈缺口，同时保留相反体验作为边界。",
        "experiment": {
            "hypothesis": "展示排队位置和进度状态会减少重复咨询。",
            "target_segment": "午高峰等待顾客",
            "intervention": "在订单页展示排队位置与阶段状态。",
            "primary_metric": "重复咨询率",
            "guardrail_metric": "订单取消率",
        },
    }


def _happy_outcomes() -> list[Mapping[str, object]]:
    return [
        _grouping(
            _subproblem(
                "排队透明度",
                ["c1-n1", "c1-n2", "c1-n3"],
                ["c1-p1"],
            )
        ),
        _actionable_draft(),
        _grouping(
            _subproblem("排队位置", ["c2-n1"], ["c2-p1"]),
            _subproblem("客服响应", ["c2-n2"], ["c2-p1"]),
            _subproblem("退款说明", ["c2-n3"], ["c2-p1"]),
        ),
        _grouping(
            _subproblem("流程清晰", ["c3-p1"], ["c3-p2", "c3-p3"]),
            _subproblem("回复及时", ["c3-p2"], ["c3-p1", "c3-p3"]),
        ),
    ]


def _write_single_case(path: Path) -> Path:
    case = {
        "id": "coherent-service",
        "expected_status": "actionable",
        "reviews": [
            {
                "id": "c1-n1",
                "text": "午高峰看不到排队进度。",
                "rating": 1,
                "aspect": "service",
                "sentiment": "negative",
                "redacted": False,
            },
            {
                "id": "c1-n2",
                "text": "等待时没有完成提示。",
                "rating": 2,
                "aspect": "service",
                "sentiment": "negative",
                "redacted": False,
            },
            {
                "id": "c1-n3",
                "text": "阶段反馈缺失。",
                "rating": 2,
                "aspect": "service",
                "sentiment": "negative",
                "redacted": False,
            },
            {
                "id": "c1-p1",
                "text": "已有进度提示很清楚。",
                "rating": 4,
                "aspect": "service",
                "sentiment": "positive",
                "redacted": False,
            },
        ],
    }
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _assert_no_sensitive_keys(value: object, *, top_level: bool = False) -> None:
    forbidden = (
        "prompt",
        "messages",
        "response",
        "raw",
        "content",
        "excerpt",
        "api_key",
        "thinking",
    )
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower()
            allowed_aggregate = normalized == "response_model_match_rate"
            if not (top_level and normalized == "raw_model") and not allowed_aggregate:
                assert not any(token in normalized for token in forbidden), normalized
            _assert_no_sensitive_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_sensitive_keys(nested)


def test_ratio_has_explicit_honest_denominators() -> None:
    assert ratio(0, 0).rate is None
    assert ratio(1, 4).rate == 0.25
    with pytest.raises(ValueError, match="invalid ratio"):
        ratio(2, 1)


def test_stage_audits_count_only_safe_facts() -> None:
    grouping_audit = audit_grouping_payload(
        _grouping(
            {
                "name": "排队问题",
                "mechanism": "排队问题二倍",
                "supporting_evidence_ids": ["known", "invented"],
                "counter_evidence_ids": [],
                "missing_information": ["缺少时段字段"],
            }
        ),
        allowed_evidence_ids={"known", "counter"},
        visible_counter_evidence_ids={"counter"},
        evidence_text_by_id={"known": "排队问题", "counter": "体验清楚"},
    )

    assert grouping_audit.unknown_citation_count == 1
    assert grouping_audit.counter_evidence_omission_count == 1
    assert grouping_audit.numeric_text_detected is True
    assert grouping_audit.mechanism_rubric_passed is False
    assert grouping_audit.supporting_citation_count == 2
    assert grouping_audit.counter_citation_count == 0

    memo_audit = audit_memo_payload(
        _actionable_draft(),
        allowed_evidence_ids={"c1-n1", "c1-n2", "c1-n3", "c1-p1"},
        expected_counter_evidence_ids={"c1-p1"},
    )
    assert memo_audit.domain_schema_valid is True
    assert memo_audit.unknown_citation_count == 0
    assert memo_audit.counter_evidence_omission_count == 0


def test_invalid_raw_schemas_keep_only_stable_retry_codes() -> None:
    sentinel = "SENSITIVE-INVALID-RAW-SCHEMA-PAYLOAD"
    grouping = audit_grouping_payload(
        {"unexpected": sentinel},
        allowed_evidence_ids=set(),
        visible_counter_evidence_ids=set(),
        evidence_text_by_id={},
    )
    memo = audit_memo_payload(
        {"unexpected": sentinel},
        allowed_evidence_ids=set(),
        expected_counter_evidence_ids=set(),
    )

    assert grouping.error_code == "INVALID_GROUPING_SCHEMA"
    assert memo.error_code == "INVALID_MEMO_SCHEMA"
    assert sentinel not in json.dumps(
        [grouping.to_dict(), memo.to_dict()], ensure_ascii=False
    )


def test_all_refused_runs_are_safe_but_not_effective() -> None:
    summary = summarize_system_runs(
        [
            SystemRunAssessment(
                terminal_status="refused",
                expected_status="refused",
                citation_count=0,
                persisted_unknown_citation_count=0,
                unsupported_numeric_claim_count=0,
                redacted_reference_count=0,
                evidence_link_pass_count=0,
                dataset_version_pass_count=0,
            )
        ]
    )

    assert summary["persisted_unknown_citation_count"] == 0
    assert summary["unsupported_numeric_claim_count"] == 0
    assert summary["redacted_reference_count"] == 0
    assert summary["effective_output_rate"]["rate"] == 0.0


@pytest.mark.anyio
async def test_recording_provider_keeps_assessments_not_model_text() -> None:
    sentinel = "MODEL-PROSE-MUST-NOT-BE-RECORDED"
    payload = _grouping(
        {
            **_subproblem("排队透明度", ["c1-n1", "c1-n2"], ["c1-p1"]),
            "mechanism": sentinel,
        }
    )
    provider = FakeMetadataProvider([payload])
    recording = RecordingProvider(provider)
    schema = {"title": "GroupingDraft"}
    user_data = {
        "dataset_version_id": "v1",
        "allowed_evidence": [
            {
                "evidence_id": "c1-n1",
                "sentiment": "negative",
                "excerpt": "排队不透明",
            },
            {
                "evidence_id": "c1-n2",
                "sentiment": "negative",
                "excerpt": "缺少提示",
            },
            {
                "evidence_id": "c1-p1",
                "sentiment": "positive",
                "excerpt": "提示清楚",
            },
        ],
    }

    returned = await recording.generate_json(
        "transient-system-instruction",
        json.dumps(user_data, ensure_ascii=False),
        schema,
    )

    assert returned is payload
    serialized_assessments = json.dumps(
        [item.to_dict() for item in recording.assessments], ensure_ascii=False
    )
    assert sentinel not in serialized_assessments
    _assert_no_sensitive_keys(
        [item.to_dict() for item in recording.assessments]
    )


@pytest.mark.anyio
async def test_recording_provider_reports_actual_response_model_match_rate() -> None:
    provider = FakeMetadataProvider([_happy_outcomes()[0]])
    provider.response_models = ["wrong-local-model:latest"]
    recording = RecordingProvider(provider)
    user_data = {
        "dataset_version_id": "v1",
        "allowed_evidence": [
            {
                "evidence_id": "c1-n1",
                "sentiment": "negative",
                "excerpt": "排队不透明",
            },
            {
                "evidence_id": "c1-n2",
                "sentiment": "negative",
                "excerpt": "缺少提示",
            },
            {
                "evidence_id": "c1-n3",
                "sentiment": "negative",
                "excerpt": "阶段反馈缺失",
            },
            {
                "evidence_id": "c1-p1",
                "sentiment": "positive",
                "excerpt": "提示清楚",
            },
        ],
    }

    await recording.generate_json(
        "transient-system-instruction",
        json.dumps(user_data, ensure_ascii=False),
        {"title": "GroupingDraft"},
    )

    summary = summarize_attempts(recording.assessments)
    assert summary["response_model_match_rate"] == {
        "numerator": 0,
        "denominator": 1,
        "rate": 0.0,
    }


@pytest.mark.anyio
async def test_recording_provider_counts_mismatch_failure_in_model_rate() -> None:
    provider = FakeMetadataProvider(
        [LocalModelError(LOCAL_MODEL_MODEL_MISMATCH)]
    )
    recording = RecordingProvider(provider)
    user_data = {"dataset_version_id": "v1", "allowed_evidence": []}

    with pytest.raises(LocalModelError) as captured:
        await recording.generate_json(
            "transient-system-instruction",
            json.dumps(user_data),
            {"title": "GroupingDraft"},
        )

    assert captured.value.code == LOCAL_MODEL_MODEL_MISMATCH
    assert recording.assessments[0].model_tag_matched is False
    assert summarize_attempts(recording.assessments)["response_model_match_rate"] == {
        "numerator": 0,
        "denominator": 1,
        "rate": 0.0,
    }


@pytest.mark.anyio
async def test_other_response_failure_is_not_counted_as_model_mismatch() -> None:
    provider = FakeMetadataProvider([LocalModelError(LOCAL_MODEL_RESPONSE_ERROR)])
    recording = RecordingProvider(provider)
    user_data = {"dataset_version_id": "v1", "allowed_evidence": []}

    with pytest.raises(LocalModelError) as captured:
        await recording.generate_json(
            "transient-system-instruction",
            json.dumps(user_data),
            {"title": "GroupingDraft"},
        )

    assert captured.value.code == LOCAL_MODEL_RESPONSE_ERROR
    assert recording.assessments[0].model_tag_matched is None
    assert summarize_attempts(recording.assessments)["response_model_match_rate"] == {
        "numerator": 0,
        "denominator": 0,
        "rate": None,
    }


@pytest.mark.anyio
async def test_recording_provider_context_failure_has_no_sensitive_chain() -> None:
    sentinel = "SENSITIVE-EVIDENCE-IN-MALFORMED-PROMPT"
    recording = RecordingProvider(FakeMetadataProvider([]))

    with pytest.raises(RuntimeError) as captured:
        await recording.generate_json(
            "transient-system-instruction",
            f'{{"private":"{sentinel}',
            {"title": "GroupingDraft"},
        )

    assert str(captured.value) == "EVALUATION_CONTEXT_ERROR"
    _assert_safe_exception(captured.value, sentinel)


@pytest.mark.anyio
async def test_recording_provider_generic_failure_has_no_model_text_chain() -> None:
    sentinel = "SENSITIVE-RAW-PROVIDER-ERROR"
    provider = FakeMetadataProvider([RuntimeError(sentinel)])
    recording = RecordingProvider(provider)
    user_data = {"dataset_version_id": "v1", "allowed_evidence": []}

    with pytest.raises(RuntimeError) as captured:
        await recording.generate_json(
            "transient-system-instruction",
            json.dumps(user_data),
            {"title": "GroupingDraft"},
        )

    assert str(captured.value) == "MODEL_PROVIDER_ERROR"
    _assert_safe_exception(captured.value, sentinel)


@pytest.mark.anyio
async def test_runner_uses_production_orchestrator_and_fresh_databases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eval import run_local_model_eval as runner

    calls: list[Path] = []
    production_function = runner.generate_decision_memo

    async def spy_generate(*args, **kwargs):
        calls.append(Path(args[0].path))
        return await production_function(*args, **kwargs)

    monkeypatch.setattr(runner, "generate_decision_memo", spy_generate)
    cases_path = Path(__file__).parents[2] / "eval" / "live_model_cases.jsonl"
    provider = FakeMetadataProvider([*_happy_outcomes(), *_happy_outcomes()])

    result = await evaluate_local_model(cases_path, provider, repeats=2)

    assert len(calls) == 6
    assert len(set(calls)) == 6
    assert all(not path.exists() for path in calls)
    assert result["case_count"] == 3
    assert result["repeat_count"] == 2
    assert [item["terminal_status"] for item in result["case_results"]] == [
        "actionable",
        "needs_evidence",
        "refused",
        "actionable",
        "needs_evidence",
        "refused",
    ]
    assert {"GroupingDraft", "MemoDraft"}.issubset(set(provider.schema_titles))
    assert len(set(provider.dataset_version_ids)) == 6
    assert provider.metadata_call_count == 2
    assert result["raw_model"]["response_model_match_rate"]["rate"] == 1.0


@pytest.mark.anyio
async def test_runner_records_retry_and_stable_failure_codes(tmp_path: Path) -> None:
    cases_path = _write_single_case(tmp_path / "case.jsonl")
    retry_provider = FakeMetadataProvider(
        [
            {"invalid": "shape"},
            _happy_outcomes()[0],
            _actionable_draft(),
        ]
    )

    retried = await evaluate_local_model(cases_path, retry_provider, repeats=1)

    assert retried["raw_model"]["after_one_retry_schema_rate"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
    }
    assert retried["runtime"]["retry_event_count"] == 1
    assert retried["raw_model"]["error_code_counts"] == {
        "INVALID_GROUPING_SCHEMA": 1
    }
    assert retried["raw_model"]["response_model_match_rate"] == {
        "numerator": 3,
        "denominator": 3,
        "rate": 1.0,
    }

    unavailable = FakeMetadataProvider([LocalModelError(LOCAL_MODEL_UNAVAILABLE)])
    failed = await evaluate_local_model(cases_path, unavailable, repeats=1)
    assert failed["case_results"][0]["terminal_status"] == "failed"
    assert failed["case_results"][0]["error_code"] == LOCAL_MODEL_UNAVAILABLE

    mismatch = FakeMetadataProvider([LocalModelError(LOCAL_MODEL_MODEL_MISMATCH)])
    mismatch_result = await evaluate_local_model(cases_path, mismatch, repeats=1)
    assert mismatch_result["case_results"][0]["error_code"] == (
        LOCAL_MODEL_MODEL_MISMATCH
    )
    assert mismatch_result["raw_model"]["response_model_match_rate"] == {
        "numerator": 0,
        "denominator": 1,
        "rate": 0.0,
    }

    invalid_citation = FakeMetadataProvider(
        [
            _grouping(
                _subproblem(
                    "排队透明度",
                    ["c1-n1", "invented"],
                    ["c1-p1"],
                )
            )
        ]
    )
    rejected = await evaluate_local_model(cases_path, invalid_citation, repeats=1)
    assert rejected["case_results"][0]["terminal_status"] == "failed"
    assert rejected["case_results"][0]["error_code"] == "UNKNOWN_EVIDENCE_ID"


@pytest.mark.anyio
async def test_result_and_report_are_strict_and_privacy_safe() -> None:
    cases_path = Path(__file__).parents[2] / "eval" / "live_model_cases.jsonl"
    result = await evaluate_local_model(
        cases_path,
        FakeMetadataProvider(_happy_outcomes()),
        repeats=1,
    )
    expected_keys = {
        "evaluation_kind",
        "generated_at",
        "model_artifact",
        "configuration",
        "case_count",
        "repeat_count",
        "raw_model",
        "validated_system",
        "runtime",
        "case_results",
        "limitations",
    }

    assert set(result) == expected_keys
    assert result["model_artifact"]["release_date"] == "2026-03-02"
    assert result["model_artifact"]["release_source"] == (
        "https://github.com/QwenLM/Qwen3.6"
    )
    serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
    _assert_no_sensitive_keys(result, top_level=True)
    assert "验证排队进度展示能否减少重复咨询。" not in serialized
    assert "午高峰排队时看不到当前位置" not in serialized

    report = render_markdown_report(result)
    assert "manual_review_status: not_performed" in report
    assert "固定合成案例，不是生产流量。" in report
    assert "refused" in report
    assert "numerator" in report
    assert "denominator" in report
    assert "response_model_match_rate" in report
    assert "结果只适用于记录的模型 digest、量化、配置与硬件。" not in report
    assert "未采集或验证硬件身份" in report
    assert "MODEL-PROSE-MUST-NOT-BE-RECORDED" not in report


@pytest.mark.anyio
async def test_runner_fails_closed_when_model_artifact_changes(
    tmp_path: Path,
) -> None:
    provider = FakeMetadataProvider([_happy_outcomes()[0], _actionable_draft()])
    provider.metadata_snapshots = [
        _metadata_snapshot(digest="sha256:artifact-before"),
        _metadata_snapshot(digest="sha256:artifact-after"),
    ]
    cases_path = _write_single_case(tmp_path / "case.jsonl")

    with pytest.raises(RuntimeError) as captured:
        await evaluate_local_model(cases_path, provider, repeats=1)

    assert str(captured.value) == "MODEL_ARTIFACT_CHANGED"
    _assert_safe_exception(captured.value, "sha256:artifact-after")
    assert provider.metadata_call_count == 2


@pytest.mark.anyio
async def test_invalid_case_json_does_not_survive_in_exception_chain(
    tmp_path: Path,
) -> None:
    sentinel = "SENSITIVE-CASE-EVIDENCE-TEXT"
    cases_path = tmp_path / "invalid-case.jsonl"
    cases_path.write_text(f'{{"private":"{sentinel}', encoding="utf-8")

    with pytest.raises(ValueError) as captured:
        await evaluate_local_model(
            cases_path,
            FakeMetadataProvider([]),
            repeats=1,
        )

    assert str(captured.value) == "INVALID_EVALUATION_CASE_LINE_1"
    _assert_safe_exception(captured.value, sentinel)


@pytest.mark.anyio
async def test_unknown_model_tag_is_rejected(tmp_path: Path) -> None:
    provider = FakeMetadataProvider(_happy_outcomes())
    provider.model_name = "unknown:latest"
    provider.settings.local_model_name = provider.model_name
    cases_path = _write_single_case(tmp_path / "case.jsonl")

    with pytest.raises(ValueError, match="UNAPPROVED_MODEL_TAG"):
        await evaluate_local_model(cases_path, provider, repeats=1)
