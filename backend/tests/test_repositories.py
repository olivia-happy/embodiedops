"""Repository tests for version-scoped, auditable local persistence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest

from signalforge.core.models import (
    DecisionMemo,
    EvidencePlan,
    MemoFacts,
    MemoGenerationJob,
)
from signalforge.db.connection import Database
from signalforge.db.repositories import (
    complete_memo_job,
    create_memo_job,
    fail_incomplete_memo_jobs,
    get_decision_memo,
    get_evidence,
    get_memo_job,
    get_running_memo_job,
    insert_dataset_version,
    list_review_evidence_by_aspect,
    save_decision_memo,
    save_feedback,
    save_trace,
    update_memo_job,
)


def _needs_evidence_memo(memo_id: str = "memo-1") -> DecisionMemo:
    return DecisionMemo(
        id=memo_id,
        dataset_version_id="v1",
        decision_status="needs_evidence",
        decision_statement="Do not take a business action yet.",
        topic="service",
        facts=MemoFacts(review_count=4, negative_count=3, negative_rate=75.0),
        supporting_evidence=[],
        counter_evidence=[],
        counter_evidence_checked=True,
        unknowns=["The candidate subproblems each have too little evidence."],
        reasoning_summary="The available service complaints describe different mechanisms.",
        evidence_plan=EvidencePlan(
            candidate_subproblems=["queue visibility", "support response time"],
            collection_fields=["wait time", "first response time"],
            minimum_evidence_per_subproblem=3,
            reassessment_condition="Reassess after each candidate has three independent examples.",
        ),
        prompt_version="decision-memo-v1",
    )


def _memo_job(
    job_id: str,
    *,
    status: str = "queued",
    created_at: datetime | None = None,
    memo_id: str | None = None,
    error_code: str | None = None,
) -> MemoGenerationJob:
    timestamp = created_at or datetime(2026, 8, 10, 9, 0)
    return MemoGenerationJob(
        id=job_id,
        dataset_version_id="v1",
        status=status,
        memo_id=memo_id,
        error_code=error_code,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_evidence_is_scoped_to_its_dataset_version(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 1)
    insert_dataset_version(db, "v2", "ASAP", "sha256:y", 1)
    db.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "v1", "服务响应太慢", 1, "service", "negative", False),
    )
    db.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "v2", "服务体验很好", 5, "service", "positive", False),
    )

    evidence = get_evidence(db, "v1", ["r1"])

    assert evidence[0]["content"] == "服务响应太慢"
    assert get_evidence(db, "v1", ["unknown"]) == []


def test_empty_evidence_ids_never_build_an_invalid_sql_clause(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()

    assert get_evidence(db, "v1", []) == []


def test_trace_and_feedback_generate_ids_and_utc_timestamps(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 1)

    trace = save_trace(
        db,
        entity_type="insight",
        entity_id="ins-1",
        dataset_version_id="v1",
        prompt_version="insight-v1",
        evidence_ids=["r1"],
        validation_status="accepted",
        latency_ms=24,
        token_estimate=13,
        provider="ollama",
        stage="grouping_evidence",
        retry_count=1,
    )
    feedback = save_feedback(
        db,
        entity_type="insight",
        entity_id="ins-1",
        decision="confirmed",
        reason="证据与原文一致。",
    )

    stored_trace = db.execute(
        "SELECT provider, stage, retry_count FROM traces WHERE id = ?", (trace.id,)
    ).fetchone()
    stored_feedback = db.execute(
        "SELECT * FROM feedback WHERE id = ?", (feedback["id"],)
    ).fetchone()

    assert trace.id
    assert isinstance(trace.created_at, datetime)
    assert stored_trace is not None
    assert stored_trace == ("ollama", "grouping_evidence", 1)
    assert stored_feedback is not None
    assert feedback["reason"] == "证据与原文一致。"


def test_apply_schema_idempotently_migrates_legacy_trace_columns(tmp_path) -> None:
    db = Database(tmp_path / "legacy-traces.duckdb")
    db.execute(
        """
        CREATE TABLE traces (
          id VARCHAR PRIMARY KEY,
          entity_type VARCHAR NOT NULL,
          entity_id VARCHAR NOT NULL,
          dataset_version_id VARCHAR NOT NULL,
          prompt_version VARCHAR NOT NULL,
          model_name VARCHAR,
          evidence_ids JSON NOT NULL,
          validation_status VARCHAR NOT NULL,
          latency_ms INTEGER NOT NULL,
          token_estimate INTEGER NOT NULL,
          created_at TIMESTAMP NOT NULL
        )
        """
    )
    db.execute(
        "INSERT INTO traces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "trace-old",
            "insight",
            "insight-old",
            "v1",
            "insight-v1",
            None,
            "[]",
            "accepted",
            7,
            11,
            datetime(2026, 8, 10, 9, 0),
        ),
    )

    db.apply_schema()
    db.apply_schema()

    columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info('traces')").fetchall()
    }
    assert columns >= {"provider", "stage", "retry_count"}
    assert db.execute(
        "SELECT provider, stage, retry_count FROM traces WHERE id = ?", ("trace-old",)
    ).fetchone() == (None, "generation", 0)


def test_save_decision_memo_replaces_current_version_memo(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    original = _needs_evidence_memo()
    replacement = _needs_evidence_memo("memo-2")

    save_decision_memo(db, original)
    save_decision_memo(db, replacement)

    assert get_decision_memo(db, "v1") == replacement
    assert db.execute(
        "SELECT COUNT(*) FROM decision_memos WHERE dataset_version_id = ?", ("v1",)
    ).fetchone() == (1,)
    assert db.execute(
        "SELECT COUNT(*) FROM decision_memo_revisions WHERE dataset_version_id = ?", ("v1",)
    ).fetchone() == (2,)
    assert get_decision_memo(db, "unknown") is None


def test_create_memo_job_atomically_reuses_the_only_active_job_per_dataset(tmp_path) -> None:
    database_path = tmp_path / "test.duckdb"
    db = Database(database_path)
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    first = _memo_job("job-1")
    second = _memo_job("job-2", created_at=first.created_at + timedelta(minutes=1))

    barrier = Barrier(2)

    def submit(job: MemoGenerationJob) -> MemoGenerationJob:
        # Separate connections and a barrier force the two inserts to race at
        # DuckDB transaction commit instead of relying on process-local state.
        with Database(database_path) as competing_db:
            barrier.wait()
            return create_memo_job(competing_db, job)

    with ThreadPoolExecutor(max_workers=2) as executor:
        returned = list(executor.map(submit, (first, second)))

    assert {job.id for job in returned} in ({"job-1"}, {"job-2"})
    assert db.execute("SELECT COUNT(*) FROM memo_generation_jobs").fetchone() == (1,)
    active = get_running_memo_job(db, "v1")
    assert active is not None
    assert active.id in {"job-1", "job-2"}
    assert get_running_memo_job(db, "unknown") is None


def test_memo_job_compare_and_set_rejects_a_stale_worker_transition(tmp_path) -> None:
    database_path = tmp_path / "test.duckdb"
    db = Database(database_path)
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    create_memo_job(db, _memo_job("job-1"))

    with Database(database_path) as advancing_worker:
        update_memo_job(
            advancing_worker,
            "job-1",
            status="analyzing_signals",
            expected_status="queued",
        )

    with pytest.raises(ValueError, match="memo job transition conflict"):
        update_memo_job(db, "job-1", status="failed", error_code="STALE", expected_status="queued")

    current = get_memo_job(db, "job-1")
    assert current is not None
    assert current.status == "analyzing_signals"


def test_completed_job_keeps_immutable_memo_revision_after_current_replacement(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    original = _needs_evidence_memo("memo-1")
    replacement = _needs_evidence_memo("memo-2")
    save_decision_memo(db, original)
    create_memo_job(db, _memo_job("job-1"))

    for status in ("analyzing_signals", "grouping_evidence", "validating_evidence"):
        update_memo_job(db, "job-1", status=status)
    completed = update_memo_job(db, "job-1", status="completed", memo_id=original.id)
    save_decision_memo(db, replacement)

    assert completed.memo_id == original.id
    assert get_memo_job(db, "job-1") == completed
    assert db.execute(
        "SELECT payload FROM decision_memo_revisions WHERE id = ?", (original.id,)
    ).fetchone() is not None
    assert get_decision_memo(db, "v1") == replacement


def test_completed_job_rejects_a_memo_id_without_a_persisted_revision(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    create_memo_job(db, _memo_job("job-1"))
    for status in ("analyzing_signals", "grouping_evidence", "validating_evidence"):
        update_memo_job(db, "job-1", status=status)

    with pytest.raises(ValueError, match="persisted memo revision"):
        update_memo_job(db, "job-1", status="completed", memo_id="missing-memo")


def test_complete_memo_job_writes_memo_and_terminal_job_in_one_transaction(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    create_memo_job(db, _memo_job("job-1"))
    for status in ("analyzing_signals", "grouping_evidence", "validating_evidence"):
        update_memo_job(db, "job-1", status=status)
    memo = _needs_evidence_memo("memo-atomic")

    completed = complete_memo_job(
        db,
        job_id="job-1",
        memo=memo,
        expected_status="validating_evidence",
    )

    assert completed.status == "completed"
    assert completed.memo_id == memo.id
    assert get_decision_memo(db, "v1") == memo
    assert get_running_memo_job(db, "v1") is None


def test_complete_memo_job_rolls_back_memo_when_job_cas_fails(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    create_memo_job(db, _memo_job("job-1"))
    update_memo_job(db, "job-1", status="analyzing_signals")
    memo = _needs_evidence_memo("memo-rolled-back")

    with pytest.raises(ValueError, match="memo job transition conflict"):
        complete_memo_job(
            db,
            job_id="job-1",
            memo=memo,
            expected_status="queued",
        )

    assert get_decision_memo(db, "v1") is None
    assert db.execute(
        "SELECT COUNT(*) FROM decision_memo_revisions WHERE id = ?", (memo.id,)
    ).fetchone() == (0,)


def test_complete_memo_job_rolls_back_when_revision_write_is_rejected(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    create_memo_job(db, _memo_job("job-1"))
    for status in ("analyzing_signals", "grouping_evidence", "validating_evidence"):
        update_memo_job(db, "job-1", status=status)
    memo = _needs_evidence_memo("memo-conflict")
    db.execute(
        """
        INSERT INTO decision_memo_revisions (
            id, dataset_version_id, payload, status, model_name, prompt_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (memo.id, "v1", "{}", "needs_evidence", None, "older", datetime(2026, 8, 10, 8, 0)),
    )

    with pytest.raises(ValueError, match="memo revision ID is immutable"):
        complete_memo_job(
            db,
            job_id="job-1",
            memo=memo,
            expected_status="validating_evidence",
        )

    job = get_memo_job(db, "job-1")
    assert job is not None and job.status == "validating_evidence"
    assert get_decision_memo(db, "v1") is None
    assert get_running_memo_job(db, "v1") == job


def test_update_memo_job_allows_only_forward_transitions_and_terminal_fields(
    tmp_path,
) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    create_memo_job(db, _memo_job("job-1"))
    save_decision_memo(db, _needs_evidence_memo("memo-1"))

    for status in (
        "analyzing_signals",
        "grouping_evidence",
        "validating_evidence",
        "generating_memo",
    ):
        updated = update_memo_job(db, "job-1", status=status)
        assert updated.status == status

    with pytest.raises(ValueError, match="illegal memo job status transition"):
        update_memo_job(db, "job-1", status="grouping_evidence")

    completed = update_memo_job(
        db,
        "job-1",
        status="completed",
        memo_id="memo-1",
    )
    assert completed.memo_id == "memo-1"
    assert completed.error_code is None

    with pytest.raises(ValueError, match="terminal memo job"):
        update_memo_job(db, "job-1", status="failed", error_code="LATE_FAILURE")


def test_update_memo_job_supports_non_actionable_completion_and_failure(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    create_memo_job(db, _memo_job("job-complete"))
    save_decision_memo(db, _needs_evidence_memo("memo-needs-evidence"))

    for status in ("analyzing_signals", "grouping_evidence", "validating_evidence"):
        update_memo_job(db, "job-complete", status=status)
    completed = update_memo_job(
        db,
        "job-complete",
        status="completed",
        memo_id="memo-needs-evidence",
    )

    create_memo_job(db, _memo_job("job-failed"))
    failed = update_memo_job(
        db,
        "job-failed",
        status="failed",
        error_code="LOCAL_MODEL_UNAVAILABLE",
    )

    assert completed.status == "completed"
    assert failed.error_code == "LOCAL_MODEL_UNAVAILABLE"


@pytest.mark.parametrize(
    ("status", "memo_id", "error_code"),
    [
        ("completed", None, None),
        ("failed", None, None),
        ("analyzing_signals", "memo-1", None),
        ("analyzing_signals", None, "UNEXPECTED"),
    ],
)
def test_update_memo_job_rejects_invalid_terminal_fields(
    tmp_path,
    status: str,
    memo_id: str | None,
    error_code: str | None,
) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    create_memo_job(db, _memo_job("job-1"))
    if status == "completed":
        for stage in ("analyzing_signals", "grouping_evidence", "validating_evidence"):
            update_memo_job(db, "job-1", status=stage)

    with pytest.raises(ValueError):
        update_memo_job(
            db,
            "job-1",
            status=status,
            memo_id=memo_id,
            error_code=error_code,
        )


def test_update_memo_job_rejects_unknown_job(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()

    with pytest.raises(ValueError, match="memo job not found"):
        update_memo_job(db, "unknown", status="failed", error_code="NOT_FOUND")


def test_fail_incomplete_jobs_after_restart(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 4)
    save_decision_memo(db, _needs_evidence_memo("memo-existing"))
    create_memo_job(
        db,
        _memo_job(
            "job-completed",
            status="completed",
            memo_id="memo-existing",
        ),
    )
    create_memo_job(db, _memo_job("job-queued"))
    insert_dataset_version(db, "v2", "ASAP", "sha256:y", 4)
    create_memo_job(
        db,
        MemoGenerationJob(
            id="job-running",
            dataset_version_id="v2",
            status="queued",
            created_at=datetime(2026, 8, 10, 9, 0),
            updated_at=datetime(2026, 8, 10, 9, 0),
        )
    )
    update_memo_job(db, "job-running", status="analyzing_signals")

    assert fail_incomplete_memo_jobs(db) == 2
    for job_id in ("job-queued", "job-running"):
        failed = get_memo_job(db, job_id)
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error_code == "SERVICE_RESTARTED"
    completed = get_memo_job(db, "job-completed")
    assert completed is not None
    assert completed.status == "completed"
    assert fail_incomplete_memo_jobs(db) == 0


def test_list_review_evidence_by_aspect_is_version_scoped_and_ordered(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 3)
    insert_dataset_version(db, "v2", "ASAP", "sha256:y", 1)
    rows = [
        ("r2", "v1", "Slow first response", 1, "service", "negative", False),
        ("r1", "v1", "Phone [PHONE]", 2, "service", "negative", True),
        ("r3", "v1", "The meal was cold", 2, "food", "negative", False),
        ("r1", "v2", "Fast response", 5, "service", "positive", False),
    ]
    for row in rows:
        db.execute("INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)", row)

    evidence = list_review_evidence_by_aspect(db, "v1", "service")

    assert [item.id for item in evidence] == ["r1", "r2"]
    assert all(item.dataset_version_id == "v1" for item in evidence)
    assert evidence[0].redacted is True
    assert evidence[1].source_type == "review"
    assert list_review_evidence_by_aspect(db, "v1", "unknown") == []


def test_list_unknown_aspect_normalizes_null_and_blank_with_version_scope(
    tmp_path,
) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 2)
    insert_dataset_version(db, "v2", "ASAP", "sha256:y", 1)
    rows = [
        ("blank", "v1", "Blank aspect", 1, "", "negative", False),
        ("null", "v1", "Null aspect", 2, None, "negative", False),
        ("foreign", "v2", "Other version", 1, None, "negative", False),
    ]
    for row in rows:
        db.execute("INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)", row)

    evidence = list_review_evidence_by_aspect(db, "v1", "unknown")

    assert [item.id for item in evidence] == ["blank", "null"]
    assert all(item.aspect == "unknown" for item in evidence)
    assert all(item.dataset_version_id == "v1" for item in evidence)
