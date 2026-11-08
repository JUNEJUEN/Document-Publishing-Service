"""HTTP clients for the external systems."""

import httpx
from pydantic import TypeAdapter, ValidationError

from .models.documents import Document

_DOCUMENT_LIST = TypeAdapter(list[Document])


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how."""


class SourceClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def list_documents(self) -> list[Document]:
        try:
            response = await self._http.get("/documents")
        except httpx.HTTPError as exc:
            raise UpstreamError(type(exc).__name__) from exc
        if not response.is_success:
            raise UpstreamError(f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise UpstreamError("answer is not JSON") from exc
        try:
            return _DOCUMENT_LIST.validate_python(data)
        except ValidationError as exc:
            raise UpstreamError("answer is not a document list") from exc
