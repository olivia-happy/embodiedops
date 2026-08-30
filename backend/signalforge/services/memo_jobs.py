"""Persisted, conflict-safe background execution for decision-memo generation."""

from __future__ import annotations

import asyncio

from signalforge.core.models import MemoGenerationJob, MemoJobStatus
from signalforge.db.connection import Database
from signalforge.db.repositories import complete_memo_job, get_memo_job, update_memo_job
from signalforge.services.local_model import LocalModelError
from signalforge.services.memo_generation import (
    MemoGenerationError,
    StructuredModelProvider,
    generate_decision_memo,
)


class _JobOwnershipLost(RuntimeError):
    """A different worker advanced or terminated the persisted job first."""


class MemoJobManager:
    """Run at most one in-process worker per persisted memo-generation job."""

    def __init__(self, db: Database, provider: StructuredModelProvider) -> None:
        self.db = db
        self.provider = provider
        self._tasks: dict[str, asyncio.Task[MemoGenerationJob | None]] = {}

    def start(self, job_id: str) -> None:
        """Schedule one job once; repeated calls for the same live task are no-ops."""

        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self.run_job(job_id), name=f"signalforge-memo-{job_id}")
        self._tasks[job_id] = task

        def discard(completed: asyncio.Task[MemoGenerationJob | None]) -> None:
            if self._tasks.get(job_id) is completed:
                self._tasks.pop(job_id, None)

        task.add_done_callback(discard)

    async def run_job(self, job_id: str) -> MemoGenerationJob | None:
        """Generate, atomically persist, or safely relinquish one persisted job."""

        job = get_memo_job(self.db, job_id)
        if job is None:
            raise ValueError(f"memo job not found: {job_id}")
        if job.status in {"completed", "failed"}:
            return job
        try:
            memo = await generate_decision_memo(
                self.db,
                job.dataset_version_id,
                self.provider,
                lambda stage: self._advance(job_id, stage),
            )
            current = get_memo_job(self.db, job_id)
            if current is None or current.status in {"completed", "failed"}:
                raise _JobOwnershipLost(job_id)
            return complete_memo_job(
                self.db,
                job_id=job_id,
                memo=memo,
                expected_status=current.status,
            )
        except asyncio.CancelledError:
            # Shutdown leaves recovery to the next lifespan's restart sweep.
            raise
        except _JobOwnershipLost:
            return get_memo_job(self.db, job_id)
        except LocalModelError as error:
            return self._fail(job_id, error.code)
        except MemoGenerationError as error:
            return self._fail(job_id, error.code)
        except ValueError as error:
            if "transition conflict" in str(error):
                return get_memo_job(self.db, job_id)
            return self._fail(job_id, "MEMO_GENERATION_FAILED")
        except Exception:
            return self._fail(job_id, "MEMO_GENERATION_FAILED")

    def _advance(self, job_id: str, target_status: MemoJobStatus) -> MemoGenerationJob:
        current = get_memo_job(self.db, job_id)
        if current is None or current.status in {"completed", "failed"}:
            raise _JobOwnershipLost(job_id)
        if current.status == target_status:
            return current
        try:
            return update_memo_job(
                self.db,
                job_id,
                status=target_status,
                expected_status=current.status,
            )
        except ValueError as error:
            latest = get_memo_job(self.db, job_id)
            if latest is not None and latest.status != current.status:
                raise _JobOwnershipLost(job_id) from error
            raise

    def _fail(self, job_id: str, error_code: str) -> MemoGenerationJob | None:
        current = get_memo_job(self.db, job_id)
        if current is None or current.status in {"completed", "failed"}:
            return current
        try:
            return update_memo_job(
                self.db,
                job_id,
                status="failed",
                error_code=error_code,
                expected_status=current.status,
            )
        except ValueError:
            # A competing worker won; it owns the terminal status and must not
            # be overwritten by this worker's stale failure.
            return get_memo_job(self.db, job_id)

    async def close(self) -> None:
        """Cancel and await workers without leaking tasks across app shutdown."""

        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
