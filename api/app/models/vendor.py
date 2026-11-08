from pydantic import ConfigDict

from .base import ApiModel


class Receipt(ApiModel):
    """The Vendor's answer to a delivery."""

    model_config = ConfigDict(strict=True)

    receipt_id: str  # the same for every retry with the same idempotency key
