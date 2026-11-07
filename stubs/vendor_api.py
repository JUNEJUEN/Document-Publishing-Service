"""Vendor API stub: the external partner. Fails on purpose about 30% of the time."""
import hashlib
import json
import os
import random
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from stub_storage import save_json

SERVICE = "vendor"
OK = "ok"
FAIL = "fail"  # nothing stored
FAIL_AFTER_STORE = "fail_after_store"  # stored, but the caller sees an error
OUTCOMES = {OK, FAIL, FAIL_AFTER_STORE}


@dataclass
class Delivery:
    receipt_id: str
    idempotency_key: str
    filename: str
    sha256: str
    size: int


def view(delivery: Delivery) -> dict:
    return {
        "receiptId": delivery.receipt_id,
        "idempotencyKey": delivery.idempotency_key,
        "filename": delivery.filename,
        "sha256": delivery.sha256,
        "size": delivery.size,
    }


def load(path: Path) -> dict[str, Delivery]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row["idempotency_key"]: Delivery(**row) for row in rows}


def create_app(
    failure_rate: float = 0.3,
    rng: random.Random | None = None,
    outcomes: Sequence[str] | None = None,
    data_file: Path | None = None,
    events: list | None = None,
) -> FastAPI:
    """`outcomes` scripts each call's result for tests; once used up, every call is OK.

    `attempts` and `events` count handled calls; a 422 never reaches the handler.
    """
    outcomes = list(outcomes) if outcomes is not None else None
    if outcomes is not None and not set(outcomes) <= OUTCOMES:
        raise ValueError(f"unknown outcomes: {set(outcomes) - OUTCOMES}")
    scripted = iter(outcomes) if outcomes is not None else None
    app = FastAPI(title="Vendor API stub")
    rng = rng or random.Random()
    deliveries = load(data_file) if data_file is not None and data_file.exists() else {}
    events = events if events is not None else []
    app.state.deliveries = deliveries
    app.state.events = events
    app.state.attempts = 0

    def next_outcome() -> str:
        if scripted is not None:
            return next(scripted, OK)
        roll = rng.random()
        if roll < failure_rate / 2:
            return FAIL
        if roll < failure_rate:
            return FAIL_AFTER_STORE
        return OK

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/files")
    async def list_files() -> list[dict]:
        events.append((SERVICE, "list", None))
        return [view(d) for d in deliveries.values()]

    @app.post("/files")
    async def receive(
        file: Annotated[UploadFile, File()],
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        events.append((SERVICE, "post", idempotency_key))
        app.state.attempts += 1
        if not idempotency_key:
            raise HTTPException(400, "Idempotency-Key header is required")
        # Read before the lookup: no await between lookup and store.
        content = await file.read()
        outcome = next_outcome()
        if outcome == FAIL:
            return JSONResponse({"detail": "vendor temporarily unavailable"}, status_code=503)
        delivery = deliveries.get(idempotency_key)
        if delivery is None:
            delivery = Delivery(
                receipt_id=uuid.uuid4().hex,
                idempotency_key=idempotency_key,
                filename=file.filename or "file",
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
            )
            deliveries[idempotency_key] = delivery
            if data_file is not None:
                save_json(data_file, [asdict(d) for d in deliveries.values()])
        if outcome == FAIL_AFTER_STORE:
            return JSONResponse({"detail": "gateway timeout"}, status_code=504)
        return {"receiptId": delivery.receipt_id, "sha256": delivery.sha256}

    return app


app = create_app(
    failure_rate=float(os.environ.get("FAILURE_RATE", "0.3")),
    data_file=Path(os.environ["DATA_FILE"]) if os.environ.get("DATA_FILE") else None,
)
