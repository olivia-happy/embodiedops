"""Regression checks for questions that the active evidence cannot answer."""

import pytest

from signalforge.db.connection import Database
from signalforge.db.repositories import insert_dataset_version
from signalforge.services.generation import GenerateInsightRequest, Refusal, generate_insight
from signalforge.services.retrieval import retrieve_evidence


@pytest.mark.parametrize("query", ["不存在的罕见问题", "无法由当前样本回答的因果关系"])
def test_unsupported_questions_refuse_generation(query: str, tmp_path) -> None:
    db = Database(tmp_path / "regression.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "asap-demo-v1", "fixture", "sha256:test", 1)
    db.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "asap-demo-v1", "服务响应太慢", 1, "service", "negative", False),
    )
    evidence = retrieve_evidence(db, "asap-demo-v1", query)

    result = generate_insight(GenerateInsightRequest("asap-demo-v1", query, evidence, db=db))

    assert isinstance(result, Refusal)
    assert result.code == "INSUFFICIENT_EVIDENCE"
