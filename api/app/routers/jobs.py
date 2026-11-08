from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from ..auth import require_token
from ..dependencies import get_runner, get_store
from ..models.jobs import JobOut, PublishRequest
from ..runner import Runner
from ..store import JobStore

router = APIRouter(dependencies=[Depends(require_token)])

# The body is read by hand, so OpenAPI needs to be told about it.
PUBLISH_BODY = {
    "required": True,
    "content": {"application/json": {"schema": PublishRequest.model_json_schema(by_alias=True)}},
}


@router.post("/publish", status_code=202, openapi_extra={"requestBody": PUBLISH_BODY})
async def publish(request: Request, runner: Annotated[Runner, Depends(get_runner)]) -> JobOut:
    # Read after the token check, so an anonymous broken body gets 401, not 422.
    try:
        order = PublishRequest.model_validate_json(await request.body())
    except ValidationError as exc:
        errors = [{**error, "loc": ("body", *error["loc"])} for error in exc.errors(include_url=False)]
        raise RequestValidationError(errors) from None
    return JobOut.model_validate(runner.publish(order.document_id, order.request_id))


@router.get("/jobs")
async def list_jobs(store: Annotated[JobStore, Depends(get_store)]) -> list[JobOut]:
    return [JobOut.model_validate(job) for job in store.newest_first()]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, store: Annotated[JobStore, Depends(get_store)]) -> JobOut:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut.model_validate(job)
