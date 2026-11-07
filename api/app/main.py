from fastapi import FastAPI

from .config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Document publishing service")
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    return app
