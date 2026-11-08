"""HTTP clients for the external systems."""
import httpx
from pydantic import TypeAdapter, ValidationError

from .models.documents import Document
from .models.vendor import Receipt

_DOCUMENT = TypeAdapter(Document)
_DOCUMENT_LIST = TypeAdapter(list[Document])
_RECEIPT = TypeAdapter(Receipt)
# Transport failures that may pass on a repeat; others (e.g. a bad URL) never will.
_TEMPORARY_TRANSPORT_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how.

    `temporary` failures (network, timeout, 429, 5xx) may succeed when repeated.
    """

    def __init__(self, message: str, *, temporary: bool = False):
        super().__init__(message)
        self.temporary = temporary


async def _send(http: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    try:
        response = await http.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise UpstreamError(type(exc).__name__, temporary=isinstance(exc, _TEMPORARY_TRANSPORT_ERRORS)) from exc
    if not response.is_success:
        temporary = response.status_code == 429 or response.is_server_error
        raise UpstreamError(f"HTTP {response.status_code}", temporary=temporary)
    return response


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
        return _parse(await _send(self._http, "GET", "/documents"), _DOCUMENT_LIST, "a document list")

    async def get_document(self, document_id: str) -> Document:
        return _parse(await _send(self._http, "GET", f"/documents/{document_id}"), _DOCUMENT, "a document")

    async def get_content(self, document_id: str) -> bytes:
        return (await _send(self._http, "GET", f"/documents/{document_id}/content")).content

    async def set_archive_link(self, document_id: str, uri: str, sha256: str) -> None:
        body = {"uri": uri, "sha256": sha256}
        await _send(self._http, "PUT", f"/documents/{document_id}/archive-link", json=body)

    async def delete_document(self, document_id: str) -> None:
        await _send(self._http, "DELETE", f"/documents/{document_id}")


class VendorClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def deliver(self, key: str, filename: str, content_type: str, content: bytes) -> str:
        """Uploads the file and returns the receipt id; the same key never stores it twice."""
        files = {"file": (filename, content, content_type)}
        response = await _send(self._http, "POST", "/files", files=files, headers={"Idempotency-Key": key})
        return _parse(response, _RECEIPT, "a receipt").receipt_id
