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
INTERRUPTED = "Interrupted by a service restart"

log = logging.getLogger(__name__)


class StepFailed(Exception):
    pass


class JobRecordUnreadable(Exception):
    """The document's job file could not be read at startup; publishing it waits for manual action."""


class NotRetryable(Exception):
    """Only a failed job whose last run has finished can be retried."""


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

        if self._store.is_blocked(document_id):
            raise JobRecordUnreadable(document_id)
        # Find any job now that has the same document id
        job = self._store.by_document(document_id)
        if job is None:
            job = Job(document_id=document_id, request_id=request_id)
            self._store.save(job)
            self._submit(job)
        return job

    def recover(self) -> None:
        """Startup (spec 4.6): interrupted jobs get an outcome; every unwritten status is written."""
        for job in self._store.newest_first():
            if job.state not in ("queued", "running"):
                continue  # finished jobs: done never runs again; failed waits for a retry
            if job.outcome is None:
                # A receipt means it was delivered before the kill.
                job.outcome, job.outcome_message = ("Published", None) if job.vendor_receipt else ("Error", INTERRUPTED)
                self._store.save(job)
            # We submit the job with outcome -> report job status back to the source CRM, not run it again
            self._submit(job)

    def retry(self, job: Job) -> None:
        """Runs a failed job again from its checkpoints (spec 5.3); no await, so it can't interleave."""
        if job.state != "failed" or self._has_task(job):
            raise NotRetryable(job.id)
        job.outcome = job.outcome_message = job.error = None
        job.status_reported = False
        job.state = "queued"
        self._store.save(job)
        self._submit(job)

    def _has_task(self, job: Job) -> bool:
        task = self._tasks.get(job.id)
        return task is not None and not task.done()

    def _submit(self, job: Job) -> None:
        self._tasks[job.id] = asyncio.create_task(self._run(job))

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
                # A job with an outcome already only needs step 6, and keeps its state until then.
                if job.outcome is None:
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
        # A retry skips each step whose checkpoint is saved (spec 4.2).
        document_id = job.document_id
        if job.archive_path is not None and not job.source_deleted:
            await self._check_archive_copy(job)

        # Step 1: get document
        if job.archive_path is None:  # steps 1 and 2 share this checkpoint
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
        if not job.archive_linked:
            uri = f"archive://{job.archive_path}"
            with _step("Linking the archive copy in Source"):
                await self._source_retry.run(lambda: self._source.set_archive_link(document_id, uri, job.sha256))
            job.archive_linked = True
            self._store.save(job)

        # Step 4: Before deletion, verify the archive copy using hash, then delete the document in source api stub
        if not job.source_deleted:
            with _step("Verifying the archive copy"):
                if not await asyncio.to_thread(self._archive.matches, job.archive_path, job.sha256):
                    raise ArchiveError("archive copy is damaged or missing")
            with _step("Deleting the original in Source"):
                await self._source_retry.run(lambda: self._source.delete_document(document_id))
            job.source_deleted = True
            self._store.save(job)

        # Step 5: Deliver file to vendor stub
        if job.vendor_receipt is None:
            await self._deliver(job)

    async def _check_archive_copy(self, job: Job) -> None:
        """Before the delete: a damaged archive copy is made again, if Source still has the original."""
        with _step("Verifying the archive copy"):
            if await asyncio.to_thread(self._archive.matches, job.archive_path, job.sha256):
                return
        with _step("Reading the document from Source"):
            document = await self._source_retry.run(lambda: self._source.get_document(job.document_id))
        if document.deleted:  # a delete whose answer was lost (spec 5.4): keep the evidence
            raise ArchiveError(DAMAGED_AFTER_DELETE)
        job.archive_path = job.sha256 = job.filename = job.content_type = None
        job.archive_linked = False
        self._store.save(job)

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
