"""Jobs in memory, mirrored to one JSON file per document."""

from pathlib import Path

from .files import write_durably
from .models.jobs import Job, utc_now


class JobStore:
    def __init__(self, folder: Path):
        # Get all jobs from storage
        folder.mkdir(parents=True, exist_ok=True)
        self._folder = folder
        jobs = (Job.model_validate_json(path.read_text(encoding="utf-8")) for path in folder.glob("*.json"))
        self._jobs = {job.document_id: job for job in jobs}

    def by_document(self, document_id: str) -> Job | None:
        return self._jobs.get(document_id)

    def get(self, job_id: str) -> Job | None:
        return next((job for job in self._jobs.values() if job.id == job_id), None)

    def newest_first(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def save(self, job: Job) -> None:
        job.updated_at = utc_now()
        write_durably(self._folder / f"{job.document_id}.json", job.model_dump_json(indent=2).encode(), ".tmp")
        self._jobs[job.document_id] = job
