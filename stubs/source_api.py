"""Source API stub: the CRM that owns a document until it is published."""
import base64
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from stub_storage import save_json

SERVICE = "source"


@dataclass
class StoredDocument:
    id: str
    filename: str
    content_type: str
    content: bytes | None  # None after delete; the record stays for the status
    status: str = "Released"
    status_message: str | None = None
    archive_uri: str | None = None
    archive_sha256: str | None = None


class ArchiveLink(BaseModel):
    uri: str
    sha256: str


class StatusUpdate(BaseModel):
    status: Literal["Published", "Error"]
    message: str | None = None


def seed_documents(count: int) -> dict[str, StoredDocument]:
    docs = {}
    for n in range(1, count + 1):
        doc_id = f"doc-{n}"
        docs[doc_id] = StoredDocument(
            id=doc_id,
            filename=f"{doc_id}.txt",
            content_type="text/plain",
            content=f"Contract {doc_id}\nReleased for partner delivery.\n".encode(),
        )
    return docs


def metadata(doc: StoredDocument) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "contentType": doc.content_type,
        "size": None if doc.content is None else len(doc.content),
        "deleted": doc.content is None,
        "status": doc.status,
        "statusMessage": doc.status_message,
        "archiveUri": doc.archive_uri,
        "archiveSha256": doc.archive_sha256,
    }


def load(path: Path) -> dict[str, StoredDocument]:
    docs = {}
    for row in json.loads(path.read_text(encoding="utf-8")):
        content = row.pop("content")
        doc = StoredDocument(**row, content=None if content is None else base64.b64decode(content))
        docs[doc.id] = doc
    return docs


def save(path: Path, docs: dict[str, StoredDocument]) -> None:
    rows = []
    for doc in docs.values():
        row = asdict(doc)
        row["content"] = None if doc.content is None else base64.b64encode(doc.content).decode()
        rows.append(row)
    save_json(path, rows)


def create_app(seed: int = 20, data_file: Path | None = None, events: list | None = None) -> FastAPI:
    """`events` collects (service, action, id) per handled call; 422s never reach a handler."""
    app = FastAPI(title="Source API stub")
    if data_file is not None and data_file.exists():
        docs = load(data_file)
    else:
        docs = seed_documents(seed)
        if data_file is not None:
            save(data_file, docs)
    events = events if events is not None else []
    app.state.documents = docs
    app.state.events = events

    def record(action: str, doc_id: str | None) -> None:
        events.append((SERVICE, action, doc_id))

    def find(doc_id: str) -> StoredDocument:
        doc = docs.get(doc_id)
        if doc is None:
            raise HTTPException(404, f"document {doc_id} not found")
        return doc

    def changed() -> None:
        if data_file is not None:
            save(data_file, docs)

    # Handlers are async so they run one at a time: saves never overlap.
    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/documents")
    async def list_documents() -> list[dict]:
        record("list", None)
        return [metadata(doc) for doc in docs.values()]

    @app.get("/documents/{doc_id}")
    async def get_document(doc_id: str) -> dict:
        record("get", doc_id)
        return metadata(find(doc_id))

    @app.get("/documents/{doc_id}/content")
    async def get_content(doc_id: str) -> Response:
        record("get_content", doc_id)
        doc = find(doc_id)
        if doc.content is None:
            raise HTTPException(410, f"document {doc_id} was deleted")
        # Raw header: Starlette would otherwise append a charset.
        return Response(content=doc.content, headers={"content-type": doc.content_type})

    @app.put("/documents/{doc_id}/archive-link")
    async def set_archive_link(doc_id: str, link: ArchiveLink) -> dict:
        record("archive_link", doc_id)
        doc = find(doc_id)
        doc.archive_uri, doc.archive_sha256 = link.uri, link.sha256
        changed()
        return metadata(doc)

    @app.delete("/documents/{doc_id}", status_code=204)
    async def delete_document(doc_id: str) -> Response:
        record("delete", doc_id)
        doc = find(doc_id)
        doc.content = None
        changed()
        return Response(status_code=204)

    @app.put("/documents/{doc_id}/status")
    async def set_status(doc_id: str, update: StatusUpdate) -> dict:
        record("status", doc_id)
        doc = find(doc_id)
        doc.status, doc.status_message = update.status, update.message
        changed()
        return metadata(doc)

    return app


app = create_app(
    seed=int(os.environ.get("SEED_DOCUMENTS", "20")),
    data_file=Path(os.environ["DATA_FILE"]) if os.environ.get("DATA_FILE") else None,
)
