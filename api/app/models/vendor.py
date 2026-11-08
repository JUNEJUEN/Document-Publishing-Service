from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Receipt(BaseModel):
    """The Vendor's answer to a delivery."""

    model_config = ConfigDict(alias_generator=to_camel, strict=True)

    receipt_id: str  # the same for every retry with the same idempotency key
