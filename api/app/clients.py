"""HTTP clients for the external systems."""
import httpx


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how."""


def _is_document_list(data: object) -> bool:
    return isinstance(data, list) and all(isinstance(d, dict) and isinstance(d.get("id"), str) for d in data)


class SourceClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def list_documents(self) -> list[dict]:
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
        if not _is_document_list(data):
            raise UpstreamError("answer is not a document list")
        return data
