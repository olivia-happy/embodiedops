"""Opt-in live evaluation through SignalForge's production memo orchestrator.

Importing this module is offline.  A model is contacted only when
``evaluate_local_model`` is explicitly called with a configured local provider
or when this file is run as a command-line program.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal, cast

from signalforge.core.config import Settings
from signalforge.core.models import DecisionMemo
from signalforge.db.connection import Database
from signalforge.db.repositories import (
    get_decision_memo,
    get_evidence,
    insert_dataset_version,
    save_decision_memo,
)
from signalforge.services.local_model import (
    LOCAL_MODEL_MODEL_MISMATCH,
    LocalModelError,
    LocalModelProvider,
)
from signalforge.services.memo_generation import (
    PROMPT_VERSION,
    MemoGenerationError,
    generate_decision_memo,
)
from signalforge.services.memo_validation import contains_model_numeric_claim

if __package__:
    from .local_model_metrics import (
        RawAttemptAssessment,
        SystemRunAssessment,
        add_usage_metadata,
        audit_grouping_payload,
        audit_memo_payload,
        failed_attempt_assessment,
        summarize_attempts,
        summarize_system_runs,
    )
else:  # pragma: no cover - exercised by the opt-in command-line entry point
    from local_model_metrics import (  # type: ignore[import-not-found]
        RawAttemptAssessment,
        SystemRunAssessment,
        add_usage_metadata,
        audit_grouping_payload,
        audit_memo_payload,
        failed_attempt_assessment,
        summarize_attempts,
        summarize_system_runs,
    )

ExpectedStatus = Literal["actionable", "needs_evidence", "refused"]
TerminalStatus = Literal["actionable", "needs_evidence", "refused", "failed"]

APPROVED_MODEL_TAG = "qwen3.5:9b"
APPROVED_RELEASE_DATE = "2026-03-02"
APPROVED_RELEASE_SOURCE = "https://github.com/QwenLM/Qwen3.6"
LIMITATIONS = [
    "固定合成案例，不是生产流量。",
    "机制深度是透明启发式，不是独立人工评分。",
    "结果仅绑定于评测首尾一致的模型 tag、digest 与记录配置；未采集或验证硬件身份。",
]
_CASE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class _ReviewRow:
    id: str
    text: str
    rating: int | None
    aspect: str
    sentiment: str
    redacted: bool


@dataclass(frozen=True, slots=True)
class _EvaluationCase:
    id: str
    expected_status: ExpectedStatus
    reviews: tuple[_ReviewRow, ...]


class RecordingProvider:
    """Convert transient generations into counts before returning the payload."""

    def __init__(self, provider: LocalModelProvider) -> None:
        self._provider = provider
        self.assessments: list[RawAttemptAssessment] = []
        self._stage_attempts: dict[str, int] = {}

    @property
    def model_name(self) -> str | None:
        return self._provider.model_name

    @property
    def provider_name(self) -> str | None:
        value = getattr(self._provider, "provider_name", None)
        return value if isinstance(value, str) else None

    def begin_run(self) -> None:
        """Reset per-stage attempt numbering without deleting prior counters."""

        self._stage_attempts.clear()

    def _next_attempt(self, stage: str) -> int:
        attempt = self._stage_attempts.get(stage, 0) + 1
        self._stage_attempts[stage] = attempt
        return attempt

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        """Call the local provider and retain no generated prose or envelopes."""

        stage = _stage_from_schema(response_schema)
        attempt_number = self._next_attempt(stage)
        context: dict[str, object] | None = None
        context_failed = False
        try:
            context = _extract_audit_context(user_prompt)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self.assessments.append(
                failed_attempt_assessment(
                    stage,
                    attempt_number=attempt_number,
                    error_code="EVALUATION_CONTEXT_ERROR",
                )
            )
            context_failed = True
        if context_failed or context is None:
            raise RuntimeError("EVALUATION_CONTEXT_ERROR")

        generation: object | None = None
        model_error_code: str | None = None
        provider_failed = False
        try:
            generation = await self._provider.generate_json_with_metadata(
                system_prompt,
                user_prompt,
                response_schema=response_schema,
            )
        except LocalModelError as error:
            self.assessments.append(
                failed_attempt_assessment(
                    stage,
                    attempt_number=attempt_number,
                    error_code=error.code,
                    model_tag_matched=(
                        False if error.code == LOCAL_MODEL_MODEL_MISMATCH else None
                    ),
                )
            )
            model_error_code = error.code
        except Exception:
            self.assessments.append(
                failed_attempt_assessment(
                    stage,
                    attempt_number=attempt_number,
                    error_code="MODEL_PROVIDER_ERROR",
                )
            )
            provider_failed = True

        if model_error_code is not None:
            raise LocalModelError(model_error_code)
        if provider_failed:
            raise RuntimeError("MODEL_PROVIDER_ERROR")
        assert generation is not None

        payload = generation.payload
        if stage == "grouping":
            assessment = audit_grouping_payload(
                payload,
                allowed_evidence_ids=context["allowed_ids"],
                visible_counter_evidence_ids=context["visible_counter_ids"],
                evidence_text_by_id=context["evidence_text_by_id"],
                attempt_number=attempt_number,
            )
        else:
            assessment = audit_memo_payload(
                payload,
                allowed_evidence_ids=context["allowed_ids"],
                expected_counter_evidence_ids=context["expected_counter_ids"],
                attempt_number=attempt_number,
            )

        usage = generation.usage
        expected_model = self.model_name
        returned_model = generation.response_model
        assessment = add_usage_metadata(
            assessment,
            input_token_count=_optional_non_negative_int(usage.prompt_eval_count),
            generated_token_count=_optional_non_negative_int(usage.eval_count),
            model_total_duration_ns=_optional_non_negative_int(
                usage.total_duration_ns
            ),
            model_load_duration_ns=_optional_non_negative_int(
                usage.load_duration_ns
            ),
            input_evaluation_duration_ns=_optional_non_negative_int(
                usage.prompt_eval_duration_ns
            ),
            generation_duration_ns=_optional_non_negative_int(
                usage.eval_duration_ns
            ),
            model_tag_matched=(
                None
                if returned_model is None or expected_model is None
                else returned_model == expected_model
            ),
        )
        self.assessments.append(assessment)
        return payload


def _stage_from_schema(
    response_schema: Mapping[str, object] | None,
) -> Literal["grouping", "memo"]:
    title = response_schema.get("title") if response_schema is not None else None
    if title == "GroupingDraft":
        return "grouping"
    if title == "MemoDraft":
        return "memo"
    raise RuntimeError("UNSUPPORTED_EVALUATION_SCHEMA")


def _extract_audit_context(user_prompt: str) -> dict[str, object]:
    data = json.loads(user_prompt)
    if not isinstance(data, dict):
        raise TypeError
    allowed_rows = data["allowed_evidence"]
    if not isinstance(allowed_rows, list):
        raise TypeError
    allowed_ids: set[str] = set()
    visible_counter_ids: set[str] = set()
    evidence_text_by_id: dict[str, str] = {}
    for row in allowed_rows:
        if not isinstance(row, dict):
            raise TypeError
        evidence_id = row.get("evidence_id")
        sentiment = row.get("sentiment")
        excerpt = row.get("excerpt")
        if not isinstance(evidence_id, str):
            raise TypeError
        allowed_ids.add(evidence_id)
        if sentiment in {"positive", "neutral"}:
            visible_counter_ids.add(evidence_id)
        if isinstance(excerpt, str):
            evidence_text_by_id[evidence_id] = excerpt

    expected_counter_ids: set[str] = set()
    selected = data.get("validated_subproblem")
    if isinstance(selected, dict):
        values = selected.get("counter_evidence_ids")
        if isinstance(values, list) and all(isinstance(item, str) for item in values):
            expected_counter_ids = set(values)
    return {
        "allowed_ids": allowed_ids,
        "visible_counter_ids": visible_counter_ids,
        "evidence_text_by_id": evidence_text_by_id,
        "expected_counter_ids": expected_counter_ids,
    }


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _load_cases(path: Path) -> list[_EvaluationCase]:
    cases: list[_EvaluationCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            case = _parse_case(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            invalid_line = line_number
            break
        cases.append(case)
    else:
        invalid_line = None
    if invalid_line is not None:
        raise ValueError(f"INVALID_EVALUATION_CASE_LINE_{invalid_line}")
    if not cases or len({item.id for item in cases}) != len(cases):
        raise ValueError("INVALID_EVALUATION_CASE_SET")
    return cases


def _parse_case(data: object) -> _EvaluationCase:
    if not isinstance(data, dict) or set(data) != {"id", "expected_status", "reviews"}:
        raise TypeError
    case_id = data["id"]
    expected_status = data["expected_status"]
    review_values = data["reviews"]
    if not isinstance(case_id, str) or _CASE_ID.fullmatch(case_id) is None:
        raise ValueError
    if expected_status not in {"actionable", "needs_evidence", "refused"}:
        raise ValueError
    if not isinstance(review_values, list) or not review_values:
        raise ValueError
    reviews = tuple(_parse_review(item) for item in review_values)
    if len({item.id for item in reviews}) != len(reviews):
        raise ValueError
    return _EvaluationCase(
        id=case_id,
        expected_status=cast(ExpectedStatus, expected_status),
        reviews=reviews,
    )


def _parse_review(data: object) -> _ReviewRow:
    expected_keys = {"id", "text", "rating", "aspect", "sentiment", "redacted"}
    if not isinstance(data, dict) or set(data) != expected_keys:
        raise TypeError
    review_id = data["id"]
    text = data["text"]
    rating = data["rating"]
    aspect = data["aspect"]
    sentiment = data["sentiment"]
    redacted = data["redacted"]
    if not isinstance(review_id, str) or not review_id:
        raise ValueError
    if not isinstance(text, str) or not text:
        raise ValueError
    if rating is not None and (
        isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5
    ):
        raise ValueError
    if not isinstance(aspect, str) or not aspect:
        raise ValueError
    if sentiment not in {"positive", "neutral", "negative", "unknown"}:
        raise ValueError
    if not isinstance(redacted, bool):
        raise ValueError
    return _ReviewRow(
        id=review_id,
        text=text,
        rating=rating,
        aspect=aspect,
        sentiment=str(sentiment),
        redacted=redacted,
    )


def _insert_case(db: Database, case: _EvaluationCase, dataset_version_id: str) -> None:
    insert_dataset_version(
        db,
        dataset_version_id,
        "fixed-synthetic-local-model-evaluation",
        f"sha256:{sha256(case.id.encode()).hexdigest()}",
        len(case.reviews),
    )
    for review in case.reviews:
        db.execute(
            "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                review.id,
                dataset_version_id,
                review.text,
                review.rating,
                review.aspect,
                review.sentiment,
                review.redacted,
            ),
        )


def _model_owned_numeric_claim(memo: DecisionMemo) -> bool:
    if memo.decision_status != "actionable":
        return False
    experiment = None
    if memo.experiment is not None:
        experiment = {
            "hypothesis": memo.experiment.hypothesis,
            "target_segment": memo.experiment.target_segment,
            "intervention": memo.experiment.intervention,
            "primary_metric": memo.experiment.primary_metric,
            "guardrail_metric": memo.experiment.guardrail_metric,
        }
    model_owned = {
        "decision_statement": memo.decision_statement,
        "subproblem": memo.subproblem,
        "supporting_evidence": [
            item.model_dump(mode="python") for item in memo.supporting_evidence
        ],
        "counter_evidence": [
            item.model_dump(mode="python") for item in memo.counter_evidence
        ],
        "unknowns": memo.unknowns,
        "reasoning_summary": memo.reasoning_summary,
        "experiment": experiment,
    }
    return contains_model_numeric_claim(model_owned)


def _assess_persisted_memo(
    db: Database,
    memo: DecisionMemo,
    *,
    expected_status: ExpectedStatus,
    dataset_version_id: str,
) -> SystemRunAssessment:
    citation_ids = [
        *(item.evidence_id for item in memo.supporting_evidence),
        *(item.evidence_id for item in memo.counter_evidence),
    ]
    unique_ids = list(dict.fromkeys(citation_ids))
    linked = get_evidence(db, dataset_version_id, unique_ids)
    linked_ids = {str(item["id"]) for item in linked}
    if unique_ids:
        placeholders = ", ".join("?" for _ in unique_ids)
        rows = db.execute(
            f"""
            SELECT id, dataset_version_id, redacted
            FROM reviews
            WHERE id IN ({placeholders})
            """,
            tuple(unique_ids),
        ).fetchall()
    else:
        rows = []
    by_id = {str(row[0]): row for row in rows}
    version_pass = sum(
        evidence_id in by_id and str(by_id[evidence_id][1]) == dataset_version_id
        for evidence_id in citation_ids
    )
    return SystemRunAssessment(
        terminal_status=memo.decision_status,
        expected_status=expected_status,
        citation_count=len(citation_ids),
        persisted_unknown_citation_count=sum(
            evidence_id not in linked_ids for evidence_id in citation_ids
        ),
        unsupported_numeric_claim_count=int(_model_owned_numeric_claim(memo)),
        redacted_reference_count=sum(
            evidence_id in by_id and bool(by_id[evidence_id][2])
            for evidence_id in citation_ids
        ),
        evidence_link_pass_count=sum(
            evidence_id in linked_ids for evidence_id in citation_ids
        ),
        dataset_version_pass_count=version_pass,
    )


def _failed_system_assessment(
    expected_status: ExpectedStatus,
) -> SystemRunAssessment:
    return SystemRunAssessment(
        terminal_status="failed",
        expected_status=expected_status,
        citation_count=0,
        persisted_unknown_citation_count=0,
        unsupported_numeric_claim_count=0,
        redacted_reference_count=0,
        evidence_link_pass_count=0,
        dataset_version_pass_count=0,
    )


def _trace_runtime(db: Database) -> dict[str, int]:
    rows = db.execute(
        """
        SELECT stage, validation_status, token_estimate
        FROM traces
        ORDER BY created_at, id
        """
    ).fetchall()
    generation_rows = [
        row for row in rows if str(row[0]) in {"grouping_evidence", "generating_memo"}
    ]
    return {
        "retry_event_count": sum(str(row[1]) == "retried" for row in rows),
        "input_token_estimate": sum(int(row[2]) for row in generation_rows),
        "json_object_received_count": sum(
            str(row[1]) == "accepted" for row in generation_rows
        ),
    }


def _sum_optional(
    attempts: Sequence[RawAttemptAssessment], field_name: str
) -> int | None:
    values = [getattr(item, field_name) for item in attempts]
    present = [value for value in values if isinstance(value, int)]
    return sum(present) if present else None


def _attempt_runtime(attempts: Sequence[RawAttemptAssessment]) -> dict[str, int | None]:
    return {
        "input_token_count": _sum_optional(attempts, "input_token_count"),
        "generated_token_count": _sum_optional(attempts, "generated_token_count"),
        "model_total_duration_ns": _sum_optional(
            attempts, "model_total_duration_ns"
        ),
        "model_load_duration_ns": _sum_optional(attempts, "model_load_duration_ns"),
        "input_evaluation_duration_ns": _sum_optional(
            attempts, "input_evaluation_duration_ns"
        ),
        "generation_duration_ns": _sum_optional(attempts, "generation_duration_ns"),
    }


def _release_for_model(model_name: str | None) -> dict[str, str]:
    if model_name != APPROVED_MODEL_TAG:
        raise ValueError("UNAPPROVED_MODEL_TAG")
    return {
        "release_date": APPROVED_RELEASE_DATE,
        "release_source": APPROVED_RELEASE_SOURCE,
    }


def _model_artifact(metadata: object, model_name: str | None) -> dict[str, object]:
    release = _release_for_model(model_name)
    if getattr(metadata, "tag", None) != APPROVED_MODEL_TAG:
        raise ValueError("UNAPPROVED_MODEL_TAG")
    return {
        "tag": metadata.tag,
        "digest": metadata.digest,
        "size_bytes": metadata.size_bytes,
        "parameter_size": metadata.parameter_size,
        "quantization": metadata.quantization,
        "artifact_format": metadata.format,
        "family": metadata.family,
        "license_id": metadata.license_id,
        "ollama_version": metadata.ollama_version,
        "loaded_size_vram": metadata.loaded_size_vram,
        "loaded_context_length": metadata.loaded_context_length,
        **release,
    }


def _configuration(provider: LocalModelProvider) -> dict[str, object]:
    settings = provider.settings
    return {
        "endpoint": settings.local_model_base_url,
        "model_name": settings.local_model_name,
        "context_length": settings.local_model_context_length,
        "temperature": settings.local_model_temperature,
        "seed": settings.local_model_seed,
        "max_output_tokens": settings.local_model_max_output_tokens,
        "reasoning_enabled": settings.local_model_think,
        "timeout_seconds": settings.local_model_timeout_seconds,
        "orchestration_version": PROMPT_VERSION,
    }


def _duration_summary(values: Sequence[int]) -> dict[str, int | float | None]:
    if not values:
        return {"count": 0, "minimum_ms": None, "median_ms": None, "maximum_ms": None}
    return {
        "count": len(values),
        "minimum_ms": min(values),
        "median_ms": round(float(median(values)), 1),
        "maximum_ms": max(values),
    }


def _runtime_summary(
    case_results: Sequence[Mapping[str, object]],
    attempts: Sequence[RawAttemptAssessment],
) -> dict[str, object]:
    cold_values = [
        int(item["end_to_end_ms"])
        for item in case_results
        if item["execution_class"] == "cold"
    ]
    warm_values = [
        int(item["end_to_end_ms"])
        for item in case_results
        if item["execution_class"] == "warm"
    ]
    return {
        "cold_end_to_end": _duration_summary(cold_values),
        "warm_end_to_end": _duration_summary(warm_values),
        "provider_attempt_count": len(attempts),
        "retry_event_count": sum(
            int(item["retry_event_count"]) for item in case_results
        ),
        "input_token_estimate": sum(
            int(item["input_token_estimate"]) for item in case_results
        ),
        "json_object_received_count": sum(
            int(item["json_object_received_count"]) for item in case_results
        ),
        **_attempt_runtime(attempts),
    }


async def evaluate_local_model(
    cases_path: Path,
    provider: LocalModelProvider,
    *,
    repeats: int,
) -> dict[str, object]:
    """Run fixed cases serially through the real two-stage memo orchestrator."""

    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise ValueError("INVALID_REPEAT_COUNT")
    cases = _load_cases(Path(cases_path))
    _release_for_model(provider.model_name)
    metadata = await provider.model_metadata()
    artifact = _model_artifact(metadata, provider.model_name)
    recording = RecordingProvider(provider)
    system_runs: list[SystemRunAssessment] = []
    case_results: list[dict[str, object]] = []

    with TemporaryDirectory(prefix="signalforge-local-eval-") as directory:
        directory_path = Path(directory)
        run_number = 0
        for repeat_index in range(repeats):
            for case_index, case in enumerate(cases):
                run_number += 1
                dataset_version_id = (
                    f"local-eval-{case.id}-repeat-{repeat_index + 1}"
                )
                db_path = directory_path / f"run-{repeat_index + 1}-{case_index + 1}.duckdb"
                started_at = perf_counter()
                stages: list[str] = []
                error_code: str | None = None
                before_attempt = len(recording.assessments)
                recording.begin_run()
                with Database(db_path) as db:
                    db.apply_schema()
                    _insert_case(db, case, dataset_version_id)
                    try:
                        memo = await generate_decision_memo(
                            db,
                            dataset_version_id,
                            recording,
                            stages.append,
                        )
                        save_decision_memo(db, memo)
                        persisted = get_decision_memo(db, dataset_version_id)
                        if persisted is None:
                            raise RuntimeError("PERSISTED_MEMO_NOT_FOUND")
                        system_assessment = _assess_persisted_memo(
                            db,
                            persisted,
                            expected_status=case.expected_status,
                            dataset_version_id=dataset_version_id,
                        )
                    except (LocalModelError, MemoGenerationError) as error:
                        error_code = error.code
                        system_assessment = _failed_system_assessment(
                            case.expected_status
                        )
                    elapsed_ms = round((perf_counter() - started_at) * 1000)
                    trace_runtime = _trace_runtime(db)

                current_attempts = recording.assessments[before_attempt:]
                system_runs.append(system_assessment)
                case_results.append(
                    {
                        "case_id": case.id,
                        "repeat_number": repeat_index + 1,
                        "expected_status": case.expected_status,
                        "terminal_status": system_assessment.terminal_status,
                        "matches_expected": (
                            system_assessment.terminal_status == case.expected_status
                        ),
                        "error_code": error_code,
                        "stage_count": len(stages),
                        "citation_count": system_assessment.citation_count,
                        "persisted_unknown_citation_count": (
                            system_assessment.persisted_unknown_citation_count
                        ),
                        "unsupported_numeric_claim_count": (
                            system_assessment.unsupported_numeric_claim_count
                        ),
                        "redacted_reference_count": (
                            system_assessment.redacted_reference_count
                        ),
                        "evidence_link_pass_count": (
                            system_assessment.evidence_link_pass_count
                        ),
                        "dataset_version_pass_count": (
                            system_assessment.dataset_version_pass_count
                        ),
                        "execution_class": "cold" if run_number == 1 else "warm",
                        "end_to_end_ms": elapsed_ms,
                        "provider_attempt_count": len(current_attempts),
                        **trace_runtime,
                        **_attempt_runtime(current_attempts),
                    }
                )

    final_metadata = await provider.model_metadata()
    if (
        getattr(final_metadata, "tag", None) != artifact["tag"]
        or getattr(final_metadata, "digest", None) != artifact["digest"]
    ):
        raise RuntimeError("MODEL_ARTIFACT_CHANGED")

    result: dict[str, object] = {
        "evaluation_kind": "live_local_model_synthetic_cases",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "model_artifact": artifact,
        "configuration": _configuration(provider),
        "case_count": len(cases),
        "repeat_count": repeats,
        "raw_model": summarize_attempts(recording.assessments),
        "validated_system": summarize_system_runs(system_runs),
        "runtime": _runtime_summary(case_results, recording.assessments),
        "case_results": case_results,
        "limitations": list(LIMITATIONS),
    }
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


def _rate_rows(section: Mapping[str, object]) -> list[str]:
    rows: list[str] = []
    for name, value in section.items():
        if not isinstance(value, Mapping) or set(value) != {
            "numerator",
            "denominator",
            "rate",
        }:
            continue
        rate_value = "null" if value["rate"] is None else str(value["rate"])
        rows.append(
            f"| `{name}` | {value['numerator']} | {value['denominator']} | "
            f"{rate_value} |"
        )
    return rows


def render_markdown_report(result: Mapping[str, object]) -> str:
    """Render a sanitized report containing only aggregate facts and codes."""

    artifact = cast(Mapping[str, object], result["model_artifact"])
    configuration = cast(Mapping[str, object], result["configuration"])
    raw_model = cast(Mapping[str, object], result["raw_model"])
    validated = cast(Mapping[str, object], result["validated_system"])
    runtime = cast(Mapping[str, object], result["runtime"])
    case_results = cast(Sequence[Mapping[str, object]], result["case_results"])
    limitations = cast(Sequence[str], result["limitations"])
    lines = [
        "# SignalForge 本地模型评测报告",
        "",
        "manual_review_status: not_performed",
        "",
        f"- 生成时间：`{result['generated_at']}`",
        f"- 模型：`{artifact['tag']}`",
        f"- digest：`{artifact['digest']}`",
        f"- 量化：`{artifact['quantization']}`",
        f"- 发布日期：`{artifact['release_date']}`",
        f"- 发布来源：{artifact['release_source']}",
        f"- 案例数：{result['case_count']}，重复次数：{result['repeat_count']}",
        "",
        "## Configuration",
        "",
    ]
    lines.extend(
        f"- `{name}`: `{json.dumps(value, ensure_ascii=False)}`"
        for name, value in configuration.items()
    )
    lines.extend(
        [
            "",
            "## Raw model metrics",
            "",
            "| metric | numerator | denominator | rate |",
            "| --- | ---: | ---: | ---: |",
            *_rate_rows(raw_model),
            "",
            f"- attempt_count: {raw_model['attempt_count']}",
            f"- error_code_counts: `{json.dumps(raw_model['error_code_counts'], ensure_ascii=False)}`",
            "",
            "## Validated system metrics",
            "",
            "| metric | numerator | denominator | rate |",
            "| --- | ---: | ---: | ---: |",
            *_rate_rows(validated),
            "",
            "- terminal_distribution: "
            f"`{json.dumps(validated['terminal_distribution'], ensure_ascii=False)}`",
            "- persisted_unknown_citation_count: "
            f"{validated['persisted_unknown_citation_count']}",
            "- unsupported_numeric_claim_count: "
            f"{validated['unsupported_numeric_claim_count']}",
            f"- redacted_reference_count: {validated['redacted_reference_count']}",
            "",
            "## Runtime",
            "",
        ]
    )
    lines.extend(
        f"- `{name}`: `{json.dumps(value, ensure_ascii=False)}`"
        for name, value in runtime.items()
    )
    lines.extend(
        [
            "",
            "## Case outcomes",
            "",
            "| case | repeat | expected | terminal | error_code | end_to_end_ms |",
            "| --- | ---: | --- | --- | --- | ---: |",
        ]
    )
    for item in case_results:
        lines.append(
            f"| `{item['case_id']}` | {item['repeat_number']} | "
            f"{item['expected_status']} | {item['terminal_status']} | "
            f"{item['error_code'] or '-'} | {item['end_to_end_ms']} |"
        )
    lines.extend(["", "## Failures and refusals", ""])
    notable = [
        item
        for item in case_results
        if item["terminal_status"] in {"failed", "refused"}
    ]
    if notable:
        lines.extend(
            f"- `{item['case_id']}` repeat {item['repeat_number']}: "
            f"{item['terminal_status']} ({item['error_code'] or 'no_error_code'})"
            for item in notable
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in limitations)
    lines.extend(
        [
            "",
            "未进行独立人工复核；这些结果不能证明生产质量或业务影响。",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Run the explicit, local-only SignalForge model evaluation."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=project_root / "eval" / "live_model_cases.jsonl",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=project_root / "eval" / "local_model-results.json",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=project_root / "docs" / "LOCAL_MODEL_EVAL_REPORT.md",
    )
    parser.add_argument("--repeats", type=int, default=3)
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    provider = LocalModelProvider(Settings())
    result = await evaluate_local_model(args.cases, provider, repeats=args.repeats)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    args.output_markdown.write_text(
        render_markdown_report(result),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "case_count": result["case_count"],
                "repeat_count": result["repeat_count"],
                "model_tag": result["model_artifact"]["tag"],
                "digest": result["model_artifact"]["digest"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - explicitly opt-in and live
    raise SystemExit(asyncio.run(_main()))
