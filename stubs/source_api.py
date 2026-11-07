"""Source API stub: the CRM that owns a document until it is published."""
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Source API stub")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    return app


app = create_app()
