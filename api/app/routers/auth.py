from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..auth import credentials_valid, issue_token, unauthorized
from ..config import Settings
from ..dependencies import get_settings

router = APIRouter()


@router.post("/oauth/token")
async def token(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> JSONResponse:
    try:
        async with request.form() as form:  # closes any uploaded temp files
            client_id, client_secret = form.get("client_id"), form.get("client_secret")
    except Exception:  # an unreadable body counts as wrong credentials
        client_id = client_secret = None
    if not (
        isinstance(client_id, str)
        and isinstance(client_secret, str)
        and credentials_valid(settings, client_id, client_secret)
    ):
        raise unauthorized("invalid client credentials")
    body = {"access_token": issue_token(settings), "token_type": "Bearer", "expires_in": settings.token_ttl_seconds}
    # Token responses must not be cached (RFC 6749, section 5.1).
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
