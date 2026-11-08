"""Runs each job as an asyncio task, a limited number at a time."""
import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from .archive import Archive, ArchiveError
from .clients import SourceClient
from .models.jobs import Job, JobState
from .store import JobStore

MAX_MESSAGE = 500


class StepFailed(Exception):
    pass


def _reason(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__)[:MAX_MESSAGE]


@contextmanager
def _step(name: str) -> Iterator[None]:
    """Names the failed step in the error message."""
    try:
        yield
    except Exception as exc:
        raise StepFailed(f"{name} failed: {_reason(exc)}") from exc


class Runner:
    def __init__(self, store: JobStore, archive: Archive, source: SourceClient, max_concurrent: int):
        self._store = store
        self._archive = archive
        self._source = source
        self._slots = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def publish(self, document_id: str, request_id: str) -> Job:
        """The document's job: the existing one, or a new one that starts now."""
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
            self._set_state(job, "running")
            try:
                await self._steps(job)
            except Exception as exc:
                self._set_state(job, "failed", _reason(exc))
            else:
                self._set_state(job, "done")

    def _set_state(self, job: Job, state: JobState, error: str | None = None) -> None:
        job.state, job.error = state, error
        self._store.save(job)

    async def _steps(self, job: Job) -> None:
        document_id = job.document_id
        with _step("Reading the document from Source"):
            document = await self._source.get_document(document_id)
            content = await self._source.get_content(document_id)
        with _step("Archiving"):
            job.archive_path, job.sha256 = await asyncio.to_thread(
                self._archive.store, document_id, document.filename, content
            )
        job.filename, job.content_type = document.filename, document.content_type
        self._store.save(job)
        with _step("Linking the archive copy in Source"):
            await self._source.set_archive_link(document_id, f"archive://{job.archive_path}", job.sha256)
        job.archive_linked = True
        self._store.save(job)
        with _step("Verifying the archive copy"):
            if not await asyncio.to_thread(self._archive.matches, job.archive_path, job.sha256):
                raise ArchiveError("archive copy is damaged or missing")
        with _step("Deleting the original in Source"):
            await self._source.delete_document(document_id)
        job.source_deleted = True
        self._store.save(job)
