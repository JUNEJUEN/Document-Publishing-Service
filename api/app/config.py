"""Settings read from environment variables."""
import os
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("CLIENT_ID", "CLIENT_SECRET", "TOKEN_SECRET")
DEFAULT_ORIGINS = ("http://localhost:8080",)


def _positive_int(env, name: str, default: int) -> int:
    try:
        value = int(env.get(name, default))
    except ValueError:
        value = 0
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    token_secret: str
    token_ttl_seconds: int = 300
    data_dir: Path = Path("/data")
    cors_origins: tuple[str, ...] = DEFAULT_ORIGINS

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        missing = [name for name in REQUIRED if not env.get(name)]
        if missing:
            raise ValueError(f"missing required settings: {', '.join(missing)}")
        ttl = _positive_int(env, "TOKEN_TTL_SECONDS", 300)
        origins = tuple(o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip())
        return cls(
            client_id=env["CLIENT_ID"],
            client_secret=env["CLIENT_SECRET"],
            token_secret=env["TOKEN_SECRET"],
            token_ttl_seconds=ttl,
            data_dir=Path(env.get("DATA_DIR", "/data")),
            cors_origins=origins or DEFAULT_ORIGINS,
        )
