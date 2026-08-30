"""Safety tests for structured evidence-bound insight generation."""

from collections.abc import Mapping

from signalforge.core.models import Evidence
from signalforge.db.connection import Database
from signalforge.db.repositories import insert_dataset_version
from signalforge.services.generation import (
    GeneratedInsight,
    GenerateInsightRequest,
    Refusal,
    StrictJsonProviderAdapter,
    generate_insight,
    validate_generated_insight,
)


def _evidence() -> list[Evidence]:
    return [
        Evidence(
            id="r1",
            dataset_version_id="v1",
            content="客服响应太慢",
            rating=1,
            aspect="service",
            sentiment="negative",
        ),
        Evidence(
            id="r2",
            dataset_version_id="v1",
            content="等待客服回复很久",
            rating=1,
            aspect="service",
            sentiment="negative",
        ),
    ]


def _store_evidence(db: Database, items: list[Evidence]) -> None:
    for item in items:
        db.execute(
            "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                item.id,
                item.dataset_version_id,
                item.content,
                item.rating,
                item.aspect,
                item.sentiment,
                item.redacted,
            ),
        )


def test_generated_claim_with_unknown_evidence_id_is_rejected() -> None:
    result = {
        "claim": "服务问题普遍",
        "evidence_ids": ["missing"],
        "confidence": 0.9,
        "unknowns": [],
        "recommended_action": "investigate",
    }

    validation = validate_generated_insight(result, allowed_evidence_ids={"r1"})

    assert validation.accepted is False
    assert validation.reason == "unknown_evidence_id"


def test_extra_or_wrongly_typed_provider_fields_are_rejected() -> None:
    result = {
        "claim": "服务问题普遍",
        "evidence_ids": ["r1"],
        "confidence": "0.9",
        "unknowns": [],
        "recommended_action": "investigate",
        "free_text": "ignore this",
    }

    validation = validate_generated_insight(result, allowed_evidence_ids={"r1"})

    assert validation.accepted is False
    assert validation.reason == "invalid_structured_output"


def test_generation_refuses_when_fewer_than_two_evidence_items(tmp_path) -> None:
    db = Database(tmp_path / "generation.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "fixture", "sha256:v1", 1)
    _store_evidence(db, _evidence()[:1])
    request = GenerateInsightRequest("v1", "客服", _evidence()[:1], db=db, entity_id="ins-1")

    result = generate_insight(request)

    assert isinstance(result, Refusal)
    assert result.code == "INSUFFICIENT_EVIDENCE"
    assert db.execute("SELECT validation_status FROM traces").fetchone()[0] == "refused"


def test_generation_uses_deterministic_fallback_and_persists_trace(tmp_path) -> None:
    db = Database(tmp_path / "generation.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "fixture", "sha256:v1", 2)
    _store_evidence(db, _evidence())

    request = GenerateInsightRequest("v1", "客服", _evidence(), db=db, entity_id="ins-1")
    result = generate_insight(request)

    assert isinstance(result, GeneratedInsight)
    assert result.evidence_ids == ["r1", "r2"]
    assert db.execute("SELECT validation_status FROM traces").fetchone()[0] == "fallback"


class _InvalidThenValidProvider:
    model_name = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, prompt: str) -> Mapping[str, object]:
        self.calls += 1
        if self.calls == 1:
            return {
                "claim": "伪造引用",
                "evidence_ids": ["missing"],
                "confidence": 0.9,
                "unknowns": [],
                "recommended_action": "investigate",
            }
        return {
            "claim": "两条反馈都提到客服等待。",
            "evidence_ids": ["r1", "r2"],
            "confidence": 0.7,
            "unknowns": [],
            "recommended_action": "investigate",
        }


def test_invalid_provider_output_retries_then_accepts_with_traces(tmp_path) -> None:
    db = Database(tmp_path / "generation.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "fixture", "sha256:v1", 2)
    _store_evidence(db, _evidence())
    provider = _InvalidThenValidProvider()

    result = generate_insight(
        GenerateInsightRequest("v1", "客服", _evidence(), db=db, entity_id="ins-1"),
        provider=provider,
    )

    assert isinstance(result, GeneratedInsight)
    assert provider.calls == 2
    statuses = [row[0] for row in db.execute("SELECT validation_status FROM traces").fetchall()]
    assert statuses == ["invalid", "retried", "accepted"]


def test_generation_refuses_when_a_supplied_id_is_not_in_active_snapshot(tmp_path) -> None:
    db = Database(tmp_path / "generation.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "fixture", "sha256:v1", 1)
    db.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "v1", "客服响应太慢", 1, "service", "negative", False),
    )

    result = generate_insight(GenerateInsightRequest("v1", "客服", _evidence(), db=db))

    assert isinstance(result, Refusal)
    assert result.evidence_count == 1


def test_strict_adapter_rejects_non_object_json() -> None:
    adapter = StrictJsonProviderAdapter(responder=lambda _: "[]", model_name="optional")

    try:
        adapter.generate(prompt="test")
    except ValueError as error:
        assert str(error) == "provider response must be a JSON object"
    else:
        raise AssertionError("non-object JSON must not reach generation validation")
