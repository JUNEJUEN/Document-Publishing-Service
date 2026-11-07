"""Vendor API stub: the external partner that receives published files."""
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Vendor API stub")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    return app


app = create_app()
