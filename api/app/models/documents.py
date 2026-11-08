from pydantic import ConfigDict

from .base import ApiModel


class Document(ApiModel):
    """A document's metadata as the Source API reports it."""

    model_config = ConfigDict(strict=True)

    id: str
    filename: str
    content_type: str
    size: int | None
    deleted: bool
    status: str
    status_message: str | None
    archive_uri: str | None
    archive_sha256: str | None
