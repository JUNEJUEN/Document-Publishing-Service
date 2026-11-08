"""HTTP clients for the external systems."""
import httpx
from pydantic import TypeAdapter, ValidationError

from .models.documents import Document

_DOCUMENT = TypeAdapter(Document)
_DOCUMENT_LIST = TypeAdapter(list[Document])


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how."""


def _parse[T](response: httpx.Response, adapter: TypeAdapter[T], what: str) -> T:
    try:
        data = response.json()
    except ValueError as exc:
        raise UpstreamError("answer is not JSON") from exc
    try:
        return adapter.validate_python(data)
    except ValidationError as exc:
        raise UpstreamError(f"answer is not {what}") from exc


class SourceClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def list_documents(self) -> list[Document]:
        return _parse(await self._call("GET", "/documents"), _DOCUMENT_LIST, "a document list")

    async def get_document(self, document_id: str) -> Document:
        return _parse(await self._call("GET", f"/documents/{document_id}"), _DOCUMENT, "a document")

    async def get_content(self, document_id: str) -> bytes:
        return (await self._call("GET", f"/documents/{document_id}/content")).content

    async def set_archive_link(self, document_id: str, uri: str, sha256: str) -> None:
        await self._call("PUT", f"/documents/{document_id}/archive-link", json={"uri": uri, "sha256": sha256})

    async def delete_document(self, document_id: str) -> None:
        await self._call("DELETE", f"/documents/{document_id}")

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamError(type(exc).__name__) from exc
        if not response.is_success:
            raise UpstreamError(f"HTTP {response.status_code}")
        return response
