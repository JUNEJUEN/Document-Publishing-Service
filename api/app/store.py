"""Jobs in memory, mirrored to one JSON file per document."""

import logging
import re
from pathlib import Path

from .files import write_durably
from .models.jobs import Job, utc_now

log = logging.getLogger(__name__)

# "{document id}.json.corrupt-{UTC timestamp}", as written by _set_aside.
SET_ASIDE_NAME = re.compile(r"(?P<document_id>.+)\.json\.corrupt-\d{8}T\d{12}Z")


def _load(path: Path) -> Job:
    """The job in a file; ValueError or OSError when it can't be trusted."""
    job = Job.model_validate_json(path.read_text(encoding="utf-8"))
    if f"{job.document_id}.json" != path.name:
        raise ValueError(f"{path.name} holds the job of {job.document_id}")
    return job


def _set_aside(path: Path) -> None:
    """Renames an unreadable job file; the timestamp keeps earlier ones."""
    target = path.with_name(f"{path.name}.corrupt-{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}")
    try:
        path.rename(target)
    except OSError:
        log.exception("Unreadable job file: could not set aside %s; its document is blocked", path.name)
    else:
        log.error("Unreadable job file %s set aside as %s; its document is blocked", path.name, target.name)


class JobStore:
    def __init__(self, folder: Path):
        # Get all jobs from storage
        folder.mkdir(parents=True, exist_ok=True)
        self._folder = folder
        for temp in folder.glob("*.json.tmp"):  # a save cut short; the .json beside it is intact
            temp.unlink()
        self._jobs: dict[str, Job] = {}
        self._blocked: set[str] = set()
        for path in folder.glob("*.json"):
            try:
                job = _load(path)
            except (ValueError, OSError):  # not JSON, not a job record, or not readable
                self._blocked.add(path.name.removesuffix(".json"))
                _set_aside(path)
            else:
                self._jobs[job.document_id] = job
        # Files set aside at earlier startups keep their documents blocked.
        names = (SET_ASIDE_NAME.fullmatch(path.name) for path in folder.iterdir())
        self._blocked.update(match["document_id"] for match in names if match)

    def by_document(self, document_id: str) -> Job | None:
        return self._jobs.get(document_id)

    def is_blocked(self, document_id: str) -> bool:
        """True when the document's job record was unreadable; unblocked by removing the .corrupt file."""
        return document_id in self._blocked

    def get(self, job_id: str) -> Job | None:
        return next((job for job in self._jobs.values() if job.id == job_id), None)

    def newest_first(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def save(self, job: Job) -> None:
        job.updated_at = utc_now()
        write_durably(self._folder / f"{job.document_id}.json", job.model_dump_json(indent=2).encode(), ".tmp")
        self._jobs[job.document_id] = job
