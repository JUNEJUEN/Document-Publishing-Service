"""Settings read from environment variables."""
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("CLIENT_ID", "CLIENT_SECRET", "TOKEN_SECRET")
DEFAULT_TOKEN_TTL = 300
DEFAULT_DATA_DIR = Path("/data")
DEFAULT_ORIGINS = ("http://localhost:8080",)
DEFAULT_SOURCE_API_URL = "http://source-stub:8001"
DEFAULT_HTTP_TIMEOUT = 10.0
DEFAULT_MAX_CONCURRENT_JOBS = 10


def _positive_number(
    env: Mapping[str, str], name: str, default: float, parse: Callable[[str], float], kind: str
) -> float:
    try:
        value = parse(env.get(name, str(default)))
    except ValueError:
        value = 0
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive {kind}")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    return int(_positive_number(env, name, default, int, "integer"))


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    return _positive_number(env, name, default, float, "number")


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    token_secret: str
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL
    data_dir: Path = DEFAULT_DATA_DIR
    cors_origins: tuple[str, ...] = DEFAULT_ORIGINS
    source_api_url: str = DEFAULT_SOURCE_API_URL
    http_timeout: float = DEFAULT_HTTP_TIMEOUT
    max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        missing = [name for name in REQUIRED if not env.get(name)]
        if missing:
            raise ValueError(f"missing required settings: {', '.join(missing)}")
        origins = tuple(o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip())
        return cls(
            client_id=env["CLIENT_ID"],
            client_secret=env["CLIENT_SECRET"],
            token_secret=env["TOKEN_SECRET"],
            token_ttl_seconds=_positive_int(env, "TOKEN_TTL_SECONDS", DEFAULT_TOKEN_TTL),
            data_dir=Path(env.get("DATA_DIR") or DEFAULT_DATA_DIR),
            cors_origins=origins or DEFAULT_ORIGINS,
            source_api_url=env.get("SOURCE_API_URL") or DEFAULT_SOURCE_API_URL,
            http_timeout=_positive_float(env, "HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT),
            max_concurrent_jobs=_positive_int(env, "MAX_CONCURRENT_JOBS", DEFAULT_MAX_CONCURRENT_JOBS),
        )
