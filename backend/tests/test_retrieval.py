"""Tests for deterministic, snapshot-scoped evidence retrieval."""

from signalforge.db.connection import Database
from signalforge.db.repositories import insert_dataset_version
from signalforge.services.retrieval import retrieve_evidence


def test_keyword_retrieval_returns_ranked_evidence_only_from_active_version(tmp_path) -> None:
    db = Database(tmp_path / "retrieval.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "fixture", "sha256:v1", 2)
    insert_dataset_version(db, "v2", "fixture", "sha256:v2", 1)
    db.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "v1", "客服响应太慢，等待很久", 1, "service", "negative", False),
    )
    db.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r2", "v1", "客服回复慢，客服一直没有回应", 1, "service", "negative", False),
    )
    db.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r3", "v2", "客服响应很快", 5, "service", "positive", False),
    )

    result = retrieve_evidence(db, "v1", "客服", limit=8)

    assert [item.id for item in result] == ["r2", "r1"]
    assert all(item.dataset_version_id == "v1" for item in result)
    assert result[0].relevance_score == 1.0


def test_keyword_retrieval_returns_empty_for_blank_or_unsupported_query(tmp_path) -> None:
    db = Database(tmp_path / "retrieval.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "fixture", "sha256:v1", 1)
    db.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "v1", "客服响应太慢", 1, "service", "negative", False),
    )

    assert retrieve_evidence(db, "v1", "") == []
    assert retrieve_evidence(db, "v1", "不存在的问题") == []

