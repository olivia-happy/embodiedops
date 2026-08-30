"""Parameterized persistence operations for versioned SignalForge records."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from signalforge.core.models import (
    DatasetVersion,
    DecisionCard,
    DecisionMemo,
    Evidence,
    Insight,
    MemoGenerationJob,
    MemoJobStatus,
    TraceRecord,
    TraceStage,
)
from signalforge.db.connection import Database
from signalforge.embodied.experiment import ReproductionExperiment
from signalforge.embodied.metrics import aggregate_task_metrics
from signalforge.embodied.models import Episode

EntityType = Literal["insight", "decision", "risk", "memo"]
FeedbackDecision = Literal["confirmed", "rejected", "edited"]
ValidationStatus = Literal[
    "accepted",
    "refused",
    "invalid",
    "retried",
    "fallback",
    "numeric_mismatch",
    "failed",
]

_TERMINAL_MEMO_JOB_STATUSES: frozenset[MemoJobStatus] = frozenset({"completed", "failed"})
_ALLOWED_MEMO_JOB_TRANSITIONS: dict[MemoJobStatus, frozenset[MemoJobStatus]] = {
    "queued": frozenset({"analyzing_signals", "failed"}),
    "analyzing_signals": frozenset({"grouping_evidence", "failed"}),
    "grouping_evidence": frozenset({"validating_evidence", "failed"}),
    "validating_evidence": frozenset({"generating_memo", "completed", "failed"}),
    "generating_memo": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
}


def _utc_now() -> datetime:
    """Return a timezone-naive UTC timestamp compatible with DuckDB TIMESTAMP."""

    return datetime.now(UTC).replace(tzinfo=None)


def _rollback_if_needed(db: Database) -> None:
    """Best-effort cleanup for DuckDB commit conflicts that auto-close a transaction."""

    try:
        db.execute("ROLLBACK")
    except Exception:
        pass


def insert_dataset_version(
    db: Database,
    dataset_version_id: str,
    source_name: str,
    file_hash: str,
    row_count: int,
    *,
    source_url: str | None = None,
    imported_at: datetime | None = None,
) -> DatasetVersion:
    """Persist one immutable import snapshot and return its domain representation."""

    timestamp = imported_at or _utc_now()
    db.execute(
        """
        INSERT INTO dataset_versions (
            id, source_name, source_url, file_hash, row_count, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (dataset_version_id, source_name, source_url, file_hash, row_count, timestamp),
    )
    return DatasetVersion(
        id=dataset_version_id,
        source_name=source_name,
        source_url=source_url,
        file_hash=file_hash,
        row_count=row_count,
        imported_at=timestamp,
    )


def get_evidence(
    db: Database, dataset_version_id: str, evidence_ids: list[str]
) -> list[dict[str, object]]:
    """Fetch review evidence only from the requested immutable dataset version."""

    if not evidence_ids:
        return []

    placeholders = ", ".join("?" for _ in evidence_ids)
    rows = db.execute(
        f"""
        SELECT id, content, rating, aspect, sentiment, redacted
        FROM reviews
        WHERE dataset_version_id = ? AND id IN ({placeholders})
        """,
        (dataset_version_id, *evidence_ids),
    ).fetchall()
    columns = ("id", "content", "rating", "aspect", "sentiment", "redacted")
    records = [dict(zip(columns, row, strict=True)) for row in rows]
    order = {evidence_id: position for position, evidence_id in enumerate(evidence_ids)}
    return sorted(records, key=lambda record: order[str(record["id"])])


def list_review_evidence_by_aspect(
    db: Database, dataset_version_id: str, aspect: str
) -> list[Evidence]:
    """Return review evidence for one aspect without crossing dataset versions."""

    rows = db.execute(
        """
        SELECT id, content, rating,
               COALESCE(NULLIF(aspect, ''), 'unknown') AS normalized_aspect,
               sentiment, redacted
        FROM reviews
        WHERE dataset_version_id = ?
          AND COALESCE(NULLIF(aspect, ''), 'unknown') = ?
        ORDER BY id
        """,
        (dataset_version_id, aspect),
    ).fetchall()
    return [
        Evidence(
            id=str(row[0]),
            dataset_version_id=dataset_version_id,
            content=str(row[1]),
            rating=int(row[2]) if row[2] is not None else None,
            aspect=str(row[3]) if row[3] is not None else None,
            sentiment=str(row[4]) if row[4] is not None else "unknown",
            redacted=bool(row[5]),
        )
        for row in rows
    ]


def get_dataset_version(db: Database, dataset_version_id: str) -> DatasetVersion | None:
    """Return one snapshot's metadata, without silently crossing version boundaries."""

    row = db.execute(
        """
        SELECT id, source_name, source_url, file_hash, row_count, imported_at
        FROM dataset_versions WHERE id = ?
        """,
        (dataset_version_id,),
    ).fetchone()
    if row is None:
        return None
    return DatasetVersion(
        id=str(row[0]),
        source_name=str(row[1]),
        source_url=str(row[2]) if row[2] is not None else None,
        file_hash=str(row[3]),
        row_count=int(row[4]),
        imported_at=row[5],
    )


def get_latest_dataset_version(db: Database) -> DatasetVersion | None:
    """Return the most recently imported immutable snapshot, if one exists."""

    row = db.execute(
        "SELECT id FROM dataset_versions ORDER BY imported_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return get_dataset_version(db, str(row[0])) if row else None


def list_dataset_versions(db: Database) -> list[DatasetVersion]:
    """Return immutable dataset versions newest first for a read-only catalog."""

    rows = db.execute(
        "SELECT id FROM dataset_versions ORDER BY imported_at DESC, id DESC"
    ).fetchall()
    return [
        version
        for (dataset_version_id,) in rows
        if (version := get_dataset_version(db, str(dataset_version_id))) is not None
    ]


def save_decision_memo(
    db: Database, memo: DecisionMemo, *, created_at: datetime | None = None
) -> DecisionMemo:
    """Save an immutable memo revision and update the one-current-memo projection."""

    timestamp = created_at or _utc_now()
    db.execute("BEGIN TRANSACTION")
    try:
        _save_decision_memo_in_transaction(db, memo, timestamp=timestamp)
        db.execute("COMMIT")
    except Exception:
        _rollback_if_needed(db)
        raise
    return memo


def _save_decision_memo_in_transaction(
    db: Database,
    memo: DecisionMemo,
    *,
    timestamp: datetime,
) -> None:
    """Persist one immutable revision and its current projection inside an open transaction."""

    payload = json.dumps(memo.model_dump(mode="json"), ensure_ascii=False)
    values = (
        memo.id,
        memo.dataset_version_id,
        payload,
        memo.decision_status,
        memo.model_name,
        memo.prompt_version,
        timestamp,
    )
    db.execute(
        """
        INSERT INTO decision_memo_revisions (
            id, dataset_version_id, payload, status, model_name, prompt_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO NOTHING
        """,
        values,
    )
    revision = db.execute(
        """
        SELECT dataset_version_id, payload
        FROM decision_memo_revisions
        WHERE id = ?
        """,
        (memo.id,),
    ).fetchone()
    revision_matches = (
        revision is not None
        and str(revision[0]) == memo.dataset_version_id
        and str(revision[1]) == payload
    )
    if not revision_matches:
        raise ValueError(f"memo revision ID is immutable: {memo.id}")
    db.execute(
        """
        INSERT INTO decision_memos (
            id, dataset_version_id, payload, status, model_name, prompt_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (dataset_version_id) DO UPDATE SET
            id = excluded.id,
            payload = excluded.payload,
            status = excluded.status,
            model_name = excluded.model_name,
            prompt_version = excluded.prompt_version,
            created_at = excluded.created_at
        """,
        values,
    )


def get_decision_memo(db: Database, dataset_version_id: str) -> DecisionMemo | None:
    """Return the current validated memo for one immutable dataset version."""

    row = db.execute(
        "SELECT payload FROM decision_memos WHERE dataset_version_id = ?",
        (dataset_version_id,),
    ).fetchone()
    if row is None:
        return None
    return DecisionMemo.model_validate(json.loads(str(row[0])))


def _memo_job_from_row(row: tuple[object, ...]) -> MemoGenerationJob:
    return MemoGenerationJob(
        id=str(row[0]),
        dataset_version_id=str(row[1]),
        status=str(row[2]),
        memo_id=str(row[3]) if row[3] is not None else None,
        error_code=str(row[4]) if row[4] is not None else None,
        created_at=row[5],
        updated_at=row[6],
    )


def create_memo_job(db: Database, job: MemoGenerationJob) -> MemoGenerationJob:
    """Create a job or atomically reuse the one active job for a dataset version."""

    if job.status in _TERMINAL_MEMO_JOB_STATUSES:
        _validate_completed_job_memo(db, job)
        _insert_memo_job(db, job)
        return job

    db.execute("BEGIN TRANSACTION")
    try:
        _insert_memo_job(db, job)
        claimed = db.execute(
            """
            INSERT INTO active_memo_generation_jobs (dataset_version_id, job_id)
            VALUES (?, ?)
            ON CONFLICT (dataset_version_id) DO NOTHING
            RETURNING job_id
            """,
            (job.dataset_version_id, job.id),
        ).fetchone()
        if claimed is not None:
            db.execute("COMMIT")
            return job
        db.execute("ROLLBACK")
    except Exception as error:
        _rollback_if_needed(db)
        existing = get_running_memo_job(db, job.dataset_version_id)
        if existing is not None:
            return existing
        raise error

    existing = get_running_memo_job(db, job.dataset_version_id)
    if existing is None:
        raise RuntimeError("active memo job guard exists without a nonterminal job")
    return existing


def _insert_memo_job(db: Database, job: MemoGenerationJob) -> None:
    db.execute(
        """
        INSERT INTO memo_generation_jobs (
            id, dataset_version_id, status, memo_id, error_code, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.id,
            job.dataset_version_id,
            job.status,
            job.memo_id,
            job.error_code,
            job.created_at,
            job.updated_at,
        ),
    )


def _validate_completed_job_memo(db: Database, job: MemoGenerationJob) -> None:
    if job.status != "completed":
        return
    revision = db.execute(
        """
        SELECT 1 FROM decision_memo_revisions
        WHERE id = ? AND dataset_version_id = ?
        """,
        (job.memo_id, job.dataset_version_id),
    ).fetchone()
    if revision is None:
        raise ValueError("completed memo job requires a persisted memo revision")


def get_memo_job(db: Database, job_id: str) -> MemoGenerationJob | None:
    """Return one persisted memo-generation job by ID."""

    row = db.execute(
        """
        SELECT id, dataset_version_id, status, memo_id, error_code, created_at, updated_at
        FROM memo_generation_jobs
        WHERE id = ?
        """,
        (job_id,),
    ).fetchone()
    return _memo_job_from_row(row) if row is not None else None


def get_running_memo_job(db: Database, dataset_version_id: str) -> MemoGenerationJob | None:
    """Return the newest nonterminal generation job for one dataset version."""

    row = db.execute(
        """
        SELECT
            job.id,
            job.dataset_version_id,
            job.status,
            job.memo_id,
            job.error_code,
            job.created_at,
            job.updated_at
        FROM active_memo_generation_jobs AS active
        JOIN memo_generation_jobs AS job ON job.id = active.job_id
        WHERE active.dataset_version_id = ?
        """,
        (dataset_version_id,),
    ).fetchone()
    return _memo_job_from_row(row) if row is not None else None


def update_memo_job(
    db: Database,
    job_id: str,
    *,
    status: MemoJobStatus,
    memo_id: str | None = None,
    error_code: str | None = None,
    updated_at: datetime | None = None,
    expected_status: MemoJobStatus | None = None,
) -> MemoGenerationJob:
    """Advance one job with compare-and-set protection against stale workers."""

    current = get_memo_job(db, job_id)
    if current is None:
        raise ValueError(f"memo job not found: {job_id}")
    if expected_status is not None and current.status != expected_status:
        raise ValueError(
            f"memo job transition conflict: expected {expected_status}, found {current.status}"
        )
    if current.status in _TERMINAL_MEMO_JOB_STATUSES:
        raise ValueError(f"cannot update terminal memo job: {job_id}")
    if status not in _ALLOWED_MEMO_JOB_TRANSITIONS[current.status]:
        raise ValueError(f"illegal memo job status transition: {current.status} -> {status}")

    timestamp = updated_at or _utc_now()
    if timestamp < current.updated_at:
        if updated_at is not None:
            raise ValueError("updated_at cannot move backwards")
        timestamp = current.updated_at
    updated = MemoGenerationJob(
        id=current.id,
        dataset_version_id=current.dataset_version_id,
        status=status,
        memo_id=memo_id,
        error_code=error_code,
        created_at=current.created_at,
        updated_at=timestamp,
    )
    _validate_completed_job_memo(db, updated)
    db.execute("BEGIN TRANSACTION")
    try:
        row = db.execute(
            """
            UPDATE memo_generation_jobs
            SET status = ?, memo_id = ?, error_code = ?, updated_at = ?
            WHERE id = ? AND status = ?
            RETURNING id, dataset_version_id, status, memo_id, error_code, created_at, updated_at
            """,
            (
                updated.status,
                updated.memo_id,
                updated.error_code,
                updated.updated_at,
                updated.id,
                current.status,
            ),
        ).fetchone()
        if row is None:
            raise ValueError("memo job transition conflict: status changed before update")
        if updated.status in _TERMINAL_MEMO_JOB_STATUSES:
            db.execute(
                """
                DELETE FROM active_memo_generation_jobs
                WHERE dataset_version_id = ? AND job_id = ?
                """,
                (updated.dataset_version_id, updated.id),
            )
        db.execute("COMMIT")
    except Exception as error:
        _rollback_if_needed(db)
        latest = get_memo_job(db, job_id)
        if latest is not None and latest.status != current.status:
            message = "memo job transition conflict: status changed before update"
            raise ValueError(message) from error
        raise
    return _memo_job_from_row(row)


def complete_memo_job(
    db: Database,
    *,
    job_id: str,
    memo: DecisionMemo,
    expected_status: MemoJobStatus | None = None,
    updated_at: datetime | None = None,
) -> MemoGenerationJob:
    """Atomically persist a memo and complete its job without a partial audit state."""

    current = get_memo_job(db, job_id)
    if current is None:
        raise ValueError(f"memo job not found: {job_id}")
    if expected_status is not None and current.status != expected_status:
        raise ValueError(
            f"memo job transition conflict: expected {expected_status}, found {current.status}"
        )
    if current.status in _TERMINAL_MEMO_JOB_STATUSES:
        raise ValueError(f"cannot update terminal memo job: {job_id}")
    if memo.dataset_version_id != current.dataset_version_id:
        raise ValueError("memo dataset version does not match memo job")
    if "completed" not in _ALLOWED_MEMO_JOB_TRANSITIONS[current.status]:
        raise ValueError(f"illegal memo job status transition: {current.status} -> completed")

    timestamp = updated_at or _utc_now()
    if timestamp < current.updated_at:
        if updated_at is not None:
            raise ValueError("updated_at cannot move backwards")
        timestamp = current.updated_at
    completed = MemoGenerationJob(
        id=current.id,
        dataset_version_id=current.dataset_version_id,
        status="completed",
        memo_id=memo.id,
        created_at=current.created_at,
        updated_at=timestamp,
    )
    db.execute("BEGIN TRANSACTION")
    try:
        _save_decision_memo_in_transaction(db, memo, timestamp=timestamp)
        row = db.execute(
            """
            UPDATE memo_generation_jobs
            SET status = ?, memo_id = ?, error_code = ?, updated_at = ?
            WHERE id = ? AND status = ?
            RETURNING id, dataset_version_id, status, memo_id, error_code, created_at, updated_at
            """,
            (
                completed.status,
                completed.memo_id,
                completed.error_code,
                completed.updated_at,
                completed.id,
                current.status,
            ),
        ).fetchone()
        if row is None:
            raise ValueError("memo job transition conflict: status changed before update")
        db.execute(
            """
            DELETE FROM active_memo_generation_jobs
            WHERE dataset_version_id = ? AND job_id = ?
            """,
            (completed.dataset_version_id, completed.id),
        )
        db.execute("COMMIT")
    except Exception as error:
        _rollback_if_needed(db)
        latest = get_memo_job(db, job_id)
        if latest is not None and latest.status != current.status:
            message = "memo job transition conflict: status changed before completion"
            raise ValueError(message) from error
        raise
    return _memo_job_from_row(row)


def fail_incomplete_memo_jobs(db: Database) -> int:
    """Mark nonterminal jobs failed after a service restart and return the count."""

    rows = db.execute(
        """
        SELECT id
        FROM memo_generation_jobs
        WHERE status NOT IN (?, ?)
        ORDER BY id
        """,
        ("completed", "failed"),
    ).fetchall()
    for row in rows:
        update_memo_job(
            db,
            str(row[0]),
            status="failed",
            error_code="SERVICE_RESTARTED",
        )
    return len(rows)


def save_insight(db: Database, insight: Insight, *, created_at: datetime | None = None) -> Insight:
    """Persist only an already validated evidence-bound insight."""

    timestamp = created_at or _utc_now()
    db.execute(
        """
        INSERT INTO insights (id, dataset_version_id, payload, status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            insight.id,
            insight.dataset_version_id,
            json.dumps(insight.model_dump(mode="json"), ensure_ascii=False),
            insight.status,
            timestamp,
        ),
    )
    return insight


def list_insights(
    db: Database, dataset_version_id: str, *, limit: int, offset: int
) -> list[Insight]:
    """Read persisted insights from one snapshot in newest-first order."""

    rows = db.execute(
        """
        SELECT payload FROM insights
        WHERE dataset_version_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (dataset_version_id, limit, offset),
    ).fetchall()
    return [Insight.model_validate(json.loads(str(row[0]))) for row in rows]


def save_decision_card(
    db: Database, card: DecisionCard, *, created_at: datetime | None = None
) -> DecisionCard:
    """Persist a decision only after the API has verified all evidence IDs."""

    timestamp = created_at or _utc_now()
    db.execute(
        """
        INSERT INTO decision_cards (id, dataset_version_id, payload, status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            card.id,
            card.dataset_version_id,
            json.dumps(card.model_dump(mode="json"), ensure_ascii=False),
            card.status,
            timestamp,
        ),
    )
    return card


def save_trace(
    db: Database,
    *,
    entity_type: EntityType,
    entity_id: str,
    dataset_version_id: str,
    prompt_version: str,
    evidence_ids: list[str],
    validation_status: ValidationStatus,
    latency_ms: int,
    token_estimate: int,
    model_name: str | None = None,
    provider: str | None = None,
    stage: TraceStage = "generation",
    retry_count: int = 0,
    trace_id: str | None = None,
    created_at: datetime | None = None,
) -> TraceRecord:
    """Store auditable generation metadata without prompts, credentials, or chain of thought."""

    record = TraceRecord(
        id=trace_id or str(uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        dataset_version_id=dataset_version_id,
        prompt_version=prompt_version,
        model_name=model_name,
        provider=provider,
        stage=stage,
        retry_count=retry_count,
        evidence_ids=evidence_ids,
        validation_status=validation_status,
        latency_ms=latency_ms,
        token_estimate=token_estimate,
        created_at=created_at or _utc_now(),
    )
    db.execute(
        """
        INSERT INTO traces (
            id, entity_type, entity_id, dataset_version_id, prompt_version, model_name,
            provider, stage, retry_count, evidence_ids, validation_status, latency_ms,
            token_estimate, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.id,
            record.entity_type,
            record.entity_id,
            record.dataset_version_id,
            record.prompt_version,
            record.model_name,
            record.provider,
            record.stage,
            record.retry_count,
            json.dumps(record.evidence_ids, ensure_ascii=False),
            record.validation_status,
            record.latency_ms,
            record.token_estimate,
            record.created_at,
        ),
    )
    return record


def save_feedback(
    db: Database,
    *,
    entity_type: EntityType,
    entity_id: str,
    decision: FeedbackDecision,
    reason: str | None = None,
    feedback_id: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, object]:
    """Persist an explicit human review without overwriting the original entity."""

    timestamp = created_at or _utc_now()
    record: dict[str, object] = {
        "id": feedback_id or str(uuid4()),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "decision": decision,
        "reason": reason,
        "created_at": timestamp,
    }
    db.execute(
        """
        INSERT INTO feedback (id, entity_type, entity_id, decision, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            record["id"],
            record["entity_type"],
            record["entity_id"],
            record["decision"],
            record["reason"],
            record["created_at"],
        ),
    )
    return record


# --- EmbodiedOps persistence -------------------------------------------------


def insert_embodied_episodes(db: Database, episodes: list[Episode]) -> int:
    """Insert immutable episodes and their events, returning newly inserted count.

    Re-importing the exact same payload is idempotent.  A changed payload for
    an existing ``(dataset_version_id, episode_id)`` is rejected instead of
    silently overwriting an audit record.
    """

    inserted = 0
    timestamp = _utc_now()
    db.execute("BEGIN TRANSACTION")
    try:
        for episode in episodes:
            payload = json.dumps(
                episode.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            )
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            existing = db.execute(
                """
                SELECT payload_sha256, payload FROM embodied_episodes
                WHERE dataset_version_id = ? AND episode_id = ?
                """,
                (episode.dataset_version_id, episode.episode_id),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != digest or str(existing[1]) != payload:
                    raise ValueError(f"embodied episode is immutable: {episode.episode_id}")
                continue
            db.execute(
                """
                INSERT INTO embodied_episodes (
                    dataset_version_id, episode_id, task_id, scene_id, payload,
                    payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.dataset_version_id,
                    episode.episode_id,
                    episode.task_id,
                    episode.scene_id,
                    payload,
                    digest,
                    timestamp,
                ),
            )
            for event in episode.events:
                db.execute(
                    """
                    INSERT INTO embodied_events (
                        dataset_version_id, episode_id, event_id, event_type,
                        event_time_s, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        episode.dataset_version_id,
                        episode.episode_id,
                        event.event_id,
                        event.event_type,
                        event.t,
                        json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                    ),
                )
            inserted += 1
        db.execute("COMMIT")
    except Exception:
        _rollback_if_needed(db)
        raise
    return inserted


def get_embodied_episode(db: Database, dataset_version_id: str, episode_id: str) -> Episode | None:
    """Read an episode only from the requested immutable dataset version."""

    row = db.execute(
        """
        SELECT payload FROM embodied_episodes
        WHERE dataset_version_id = ? AND episode_id = ?
        """,
        (dataset_version_id, episode_id),
    ).fetchone()
    return Episode.model_validate(json.loads(str(row[0]))) if row is not None else None


def list_embodied_task_metrics(db: Database, dataset_version_id: str) -> list[dict[str, object]]:
    """Aggregate deterministic task metrics without crossing dataset versions."""

    rows = db.execute(
        """
        SELECT task_id, payload FROM embodied_episodes
        WHERE dataset_version_id = ? ORDER BY task_id, episode_id
        """,
        (dataset_version_id,),
    ).fetchall()
    grouped: dict[str, list[Episode]] = {}
    for task_id, payload in rows:
        grouped.setdefault(str(task_id), []).append(
            Episode.model_validate(json.loads(str(payload)))
        )
    return [
        {"task_id": task_id, "metrics": aggregate_task_metrics(episodes).model_dump(mode="json")}
        for task_id, episodes in sorted(grouped.items())
    ]


def create_embodied_diagnosis_job(
    db: Database,
    *,
    dataset_version_id: str,
    episode_id: str,
    diagnosis_id: str,
    created_at: datetime | None = None,
) -> dict[str, object]:
    """Create an immutable queued diagnosis job and its initial audit trace."""

    if get_embodied_episode(db, dataset_version_id, episode_id) is None:
        raise ValueError(f"embodied episode not found: {episode_id}")
    existing_for_episode = db.execute(
        """
        SELECT id, dataset_version_id, episode_id, status
        FROM embodied_diagnoses
        WHERE dataset_version_id = ? AND episode_id = ?
        """,
        (dataset_version_id, episode_id),
    ).fetchone()
    if existing_for_episode is not None:
        return {
            "id": str(existing_for_episode[0]),
            "dataset_version_id": str(existing_for_episode[1]),
            "episode_id": str(existing_for_episode[2]),
            "status": str(existing_for_episode[3]),
        }
    timestamp = created_at or _utc_now()
    trace = {
        "trace_id": str(uuid4()),
        "provider": "none",
        "stage": "queued",
        "retry_count": 0,
        "latency_ms": 0,
        "validation_code": "QUEUED",
    }
    payload = (
        diagnosis_id,
        dataset_version_id,
        episode_id,
        "queued",
        None,
        "QUEUED",
        json.dumps(trace, ensure_ascii=False),
        timestamp,
        timestamp,
    )
    db.execute(
        """
        INSERT INTO embodied_diagnoses (
            id, dataset_version_id, episode_id, status, payload,
            validation_code, trace, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO NOTHING
        """,
        payload,
    )
    row = db.execute(
        "SELECT id, dataset_version_id, episode_id, status FROM embodied_diagnoses WHERE id = ?",
        (diagnosis_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("diagnosis job was not persisted")
    return {
        "id": str(row[0]),
        "dataset_version_id": str(row[1]),
        "episode_id": str(row[2]),
        "status": str(row[3]),
    }


def get_embodied_diagnosis(db: Database, diagnosis_id: str) -> dict[str, object] | None:
    """Return diagnosis payload and its non-sensitive provider trace."""

    row = db.execute(
        """
        SELECT id, dataset_version_id, episode_id, status, payload,
               validation_code, trace, created_at, updated_at
        FROM embodied_diagnoses WHERE id = ?
        """,
        (diagnosis_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "dataset_version_id": str(row[1]),
        "episode_id": str(row[2]),
        "status": str(row[3]),
        "payload": json.loads(str(row[4])) if row[4] is not None else None,
        "validation_code": str(row[5]),
        "trace": json.loads(str(row[6])),
        "created_at": row[7],
        "updated_at": row[8],
    }


def save_embodied_diagnosis(
    db: Database,
    *,
    diagnosis_id: str,
    payload: dict[str, object] | None,
    status: str,
    validation_code: str,
    trace: dict[str, object],
    updated_at: datetime | None = None,
) -> dict[str, object]:
    """Atomically complete/fail a job; failed jobs never persist partial payloads."""

    if status not in {"completed", "failed"}:
        raise ValueError("embodied diagnosis status must be terminal")
    if status == "failed":
        payload = None
    current = get_embodied_diagnosis(db, diagnosis_id)
    if current is None:
        raise ValueError(f"embodied diagnosis not found: {diagnosis_id}")
    if current["status"] != "queued":
        return current
    timestamp = updated_at or _utc_now()
    db.execute("BEGIN TRANSACTION")
    try:
        db.execute(
            """
            UPDATE embodied_diagnoses
            SET status = ?, payload = ?, validation_code = ?, trace = ?, updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (
                status,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                validation_code,
                json.dumps(trace, ensure_ascii=False),
                timestamp,
                diagnosis_id,
            ),
        )
        db.execute("COMMIT")
    except Exception:
        _rollback_if_needed(db)
        raise
    return get_embodied_diagnosis(db, diagnosis_id)  # type: ignore[return-value]


def save_embodied_experiment(
    db: Database, experiment: ReproductionExperiment, *, diagnosis_id: str
) -> dict[str, object]:
    """Persist one simulation-only experiment definition idempotently."""

    payload = json.dumps(experiment.model_dump(mode="json"), ensure_ascii=False)
    db.execute(
        """
        INSERT INTO embodied_experiments (
            diagnosis_id, experiment_id, dataset_version_id, episode_id, payload, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (diagnosis_id) DO NOTHING
        """,
        (
            diagnosis_id,
            experiment.experiment_id,
            experiment.dataset_version_id,
            experiment.episode_id,
            payload,
            _utc_now(),
        ),
    )
    row = db.execute(
        """
        SELECT diagnosis_id, experiment_id, payload
        FROM embodied_experiments WHERE diagnosis_id = ?
        """,
        (diagnosis_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("embodied experiment was not persisted")
    return {
        "diagnosis_id": str(row[0]),
        "experiment_id": str(row[1]),
        "payload": json.loads(str(row[2])),
    }


def get_embodied_experiment(db: Database, diagnosis_id: str) -> dict[str, object] | None:
    row = db.execute(
        """
        SELECT diagnosis_id, experiment_id, dataset_version_id, episode_id,
               payload, created_at
        FROM embodied_experiments WHERE diagnosis_id = ?
        """,
        (diagnosis_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "diagnosis_id": str(row[0]),
        "experiment_id": str(row[1]),
        "dataset_version_id": str(row[2]),
        "episode_id": str(row[3]),
        "payload": json.loads(str(row[4])),
        "created_at": row[5],
    }
