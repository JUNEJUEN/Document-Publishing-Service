from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Document(BaseModel):
    """A document's metadata as the Source API reports it."""

    model_config = ConfigDict(alias_generator=to_camel, strict=True)

    id: str
    filename: str
    content_type: str
    size: int | None
    deleted: bool
    status: str
    status_message: str | None
    archive_uri: str | None
    archive_sha256: str | None
