from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

JobState = Literal["queued", "running", "done", "failed"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Job(BaseModel):
    """The job record, saved as /data/jobs/{document_id}.json."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    request_id: str
    document_id: str
    state: JobState = "queued"
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    # Checkpoints of steps 2-4.
    filename: str | None = None
    content_type: str | None = None
    archive_path: str | None = None
    sha256: str | None = None
    archive_linked: bool = False
    source_deleted: bool = False


class JobOut(BaseModel):
    """A job as the API shows it."""

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True, from_attributes=True)

    id: str
    request_id: str
    document_id: str
    state: JobState
    error: str | None
    created_at: datetime
    updated_at: datetime
    archive_path: str | None


class PublishRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    # It becomes a folder name, so it can't be ".." or contain slashes.
    document_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")]
    request_id: Annotated[str, Field(min_length=1, max_length=200)]
