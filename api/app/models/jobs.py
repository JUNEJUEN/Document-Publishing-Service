from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import ConfigDict, Field

from .base import ApiModel

JobState = Literal["queued", "running", "done", "failed"]
Outcome = Literal["Published", "Error"]  # the status written back to Source


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobOut(ApiModel):
    """A job as the API shows it."""

    # Lets models load snake_case data: job files, and view() in the jobs router.
    model_config = ConfigDict(validate_by_name=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    request_id: str
    document_id: str
    state: JobState = "queued"
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    archive_path: str | None = None
    vendor_receipt: str | None = None  # the Vendor's receipt id, proof of delivery; None until the Vendor confirms
    vendor_attempts: int = 0  # a counter, never reset
    status_reported: bool = False  # the outcome is written to Source


class JobView(JobOut):
    """A job as every job endpoint answers: its fields, plus whether Retry is allowed now."""

    can_retry: bool


class Job(JobOut):
    """The job record, saved as /data/jobs/{document_id}.json: the API fields plus internal checkpoints."""

    filename: str | None = None
    content_type: str | None = None
    sha256: str | None = None
    archive_linked: bool = False
    source_deleted: bool = False
    outcome: Outcome | None = None  # set once steps 1-5 end
    outcome_message: str | None = None  # why an Error; at most 500 characters


class PublishRequest(ApiModel):
    # It becomes a folder name, so it can't be ".." or contain slashes.
    document_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")]
    request_id: Annotated[str, Field(min_length=1, max_length=200)]
