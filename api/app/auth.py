"""Client-credentials tokens. Any problem with a token is a 401, never a 500."""

import hmac
import time
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request

from .config import Settings
from .dependencies import get_settings

ALGORITHM = "HS256"
AUDIENCE = "publishing-api"


def credentials_valid(settings: Settings, client_id: str, client_secret: str) -> bool:
    # Bytes, so any characters compare in constant time without TypeError.
    id_ok = hmac.compare_digest(client_id.encode(), settings.client_id.encode())
    secret_ok = hmac.compare_digest(client_secret.encode(), settings.client_secret.encode())
    return id_ok and secret_ok


def issue_token(settings: Settings, now: float | None = None) -> str:
    issued = int(time.time() if now is None else now)
    claims = {"sub": settings.client_id, "aud": AUDIENCE, "iat": issued, "exp": issued + settings.token_ttl_seconds}
    return jwt.encode(claims, settings.token_secret, algorithm=ALGORITHM)


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def require_token(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> str:
    """Dependency: the client id from a valid bearer token, else 401."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthorized("invalid or expired token")
    try:
        claims = jwt.decode(
            token.strip(),
            settings.token_secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["sub", "aud", "iat", "exp"]},
        )
    except Exception:
        raise unauthorized("invalid or expired token") from None
    return claims["sub"]
