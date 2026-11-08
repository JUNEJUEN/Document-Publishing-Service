from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_token
from ..clients import SourceClient, UpstreamError
from ..dependencies import get_source
from ..models.documents import Document

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/documents")
async def list_documents(source: Annotated[SourceClient, Depends(get_source)]) -> list[Document]:
    try:
        return await source.list_documents()
    except UpstreamError as exc:
        raise HTTPException(502, f"Source API unavailable: {exc}") from None
