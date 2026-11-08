"""Runs each job as an asyncio task, a limited number at a time."""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from .archive import Archive, ArchiveError
from .backoff import RetryPolicy
from .clients import SourceClient, UpstreamError, VendorClient
from .config import Settings
from .models.jobs import Job, JobState
from .store import JobStore

MAX_MESSAGE = 500
DAMAGED_AFTER_DELETE = "Archive copy damaged or missing after the original was deleted; manual action needed"
NOT_IN_SOURCE = "Document not found in Source; the status cannot be written."
STATUS_PENDING = "Status not written yet: "

log = logging.getLogger(__name__)


class StepFailed(Exception):
    pass


def _reason(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__)[:MAX_MESSAGE]


def _final_state(job: Job) -> tuple[JobState, str | None]:
    """The state and error that match a written outcome."""
    return ("done", None) if job.outcome == "Published" else ("failed", job.outcome_message)


@contextmanager
def _step(name: str) -> Iterator[None]:
    """Names the failed step in the error message."""
    try:
        yield
    except Exception as exc:
        raise StepFailed(f"{name} failed: {_reason(exc)}") from exc


class Runner:
    """Job runner"""

    def __init__(
        self, store: JobStore, archive: Archive, source: SourceClient, vendor: VendorClient, settings: Settings
    ):
        self._store = store
        self._archive = archive
        self._source = source
        self._vendor = vendor
        delays = (settings.retry_base_delay, settings.retry_max_delay)
        self._source_retry = RetryPolicy(settings.source_max_attempts, *delays)
        self._vendor_retry = RetryPolicy(settings.vendor_max_attempts, *delays)
        self._slots = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def publish(self, document_id: str, request_id: str) -> Job:
        """The document's job: the existing one, or a new one that starts now."""

        # Find any job now that has the same document id
        job = self._store.by_document(document_id)
        if job is None:
            job = Job(document_id=document_id, request_id=request_id)
            self._store.save(job)
            self._tasks[job.id] = asyncio.create_task(self._run(job))
        return job

    async def wait_idle(self) -> None:
        """Waits until every job task has finished."""
        while pending := [task for task in self._tasks.values() if not task.done()]:
            await asyncio.wait(pending)

    async def shutdown(self) -> None:
        """Cancels the running tasks; job states are left as they are."""
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self, job: Job) -> None:
        async with self._slots:
            # Outer guard: no unexpected error ends a run silently. Cancellation passes through.
            try:
                job.state = "running"
                self._store.save(job)
                await self._decide_outcome(job)
                await self._report(job)
            except Exception as exc:
                log.exception("Job %s: unexpected error", job.id)
                self._rescue(job, exc)

    async def _decide_outcome(self, job: Job) -> None:
        """Runs steps 1-5; any failure becomes an Error outcome."""
        try:
            await self._steps(job)
        except Exception as exc:
            job.outcome, job.outcome_message = "Error", _reason(exc)
        else:
            job.outcome, job.outcome_message = "Published", None
        self._store.save(job)

    async def _report(self, job: Job) -> None:
        """Step 6: writes the outcome back to Source, then ends the run."""
        try:
            await self._source_retry.run(
                lambda: self._source.set_status(job.document_id, job.outcome, job.outcome_message)
            )
        except UpstreamError as exc:
            if exc.status == 404:  # nothing to write to: the job ends without a status (R4 exception)
                message = f"{NOT_IN_SOURCE} {job.outcome_message}" if job.outcome_message else NOT_IN_SOURCE
                self._finish(job, "failed", message)
            else:
                self._finish(job, "running", f"{STATUS_PENDING}{_reason(exc)}")
        else:
            self._finish(job, *_final_state(job), reported=True)

    def _rescue(self, job: Job, exc: Exception) -> None:
        """Leaves a job hit by an unexpected error in a safe, saved state."""
        reason = _reason(exc)
        try:
            if job.status_reported:
                self._finish(job, *_final_state(job), reported=True)
                return
            if job.outcome is None:
                job.outcome, job.outcome_message = "Error", reason
            self._finish(job, "running", f"{STATUS_PENDING}{reason}")
        except Exception:  # e.g. the disk still refuses; the job stays as it is in memory
            log.exception("Job %s: could not save after an unexpected error", job.id)

    def _finish(self, job: Job, state: JobState, error: str | None, reported: bool = False) -> None:
        """The run's last change: state, error and status_reported are always saved together."""
        job.state, job.error, job.status_reported = state, error, reported
        self._store.save(job)

    async def _steps(self, job: Job) -> None:
        """Steps to run a job"""
        document_id = job.document_id

        # Step 1: get document
        with _step("Reading the document from Source"):
            document = await self._source_retry.run(lambda: self._source.get_document(document_id))
            content = await self._source_retry.run(lambda: self._source.get_content(document_id))

        # Step 2: archive the document and update the job status
        with _step("Archiving"):
            job.archive_path, job.sha256 = await asyncio.to_thread(
                self._archive.store, document_id, document.filename, content
            )
        job.filename, job.content_type = document.filename, document.content_type
        self._store.save(job)

        # Step 3: Update the archive link in source api stub
        uri = f"archive://{job.archive_path}"
        with _step("Linking the archive copy in Source"):
            await self._source_retry.run(lambda: self._source.set_archive_link(document_id, uri, job.sha256))
        job.archive_linked = True
        self._store.save(job)

        # Step 4: Before deletion, verify the archive copy using hash, then delete the document in source api stub
        with _step("Verifying the archive copy"):
            if not await asyncio.to_thread(self._archive.matches, job.archive_path, job.sha256):
                raise ArchiveError("archive copy is damaged or missing")
        with _step("Deleting the original in Source"):
            await self._source_retry.run(lambda: self._source.delete_document(document_id))
        job.source_deleted = True
        self._store.save(job)

        # Step 5: Deliver file to vendor stub
        await self._deliver(job)

    async def _deliver(self, job: Job) -> None:
        """Step 5: sends the archive copy, now the only copy, to the Vendor."""
        content = await asyncio.to_thread(self._archive.read, job.archive_path, job.sha256)
        if content is None:
            raise ArchiveError(DAMAGED_AFTER_DELETE)

        async def attempt() -> str:
            job.vendor_attempts += 1
            self._store.save(job)  # the UI shows the attempts while they happen
            return await self._vendor.deliver(job.id, job.filename, job.content_type, content)

        with _step("Delivering to the Vendor"):
            job.vendor_receipt = await self._vendor_retry.run(attempt)
        self._store.save(job)
