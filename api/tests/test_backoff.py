import httpx
import pytest

from app.backoff import RetryPolicy
from app.clients import SourceClient, UpstreamError


def failing(*errors: Exception):
    """A call that raises each error in turn, then answers "ok"; it counts its calls."""
    calls = []

    async def call() -> str:
        calls.append(None)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return "ok"

    return call, calls


async def no_sleep(seconds: float) -> None:
    pass


def policy(attempts: int = 5, base_delay: float = 0.5, max_delay: float = 8, **changes) -> RetryPolicy:
    changes.setdefault("sleep", no_sleep)
    return RetryPolicy(attempts, base_delay, max_delay, **changes)


TEMPORARY = UpstreamError("HTTP 503", temporary=True)


async def test_temporary_errors_are_retried_until_success():
    call, calls = failing(TEMPORARY, TEMPORARY)
    assert await policy().run(call) == "ok"
    assert len(calls) == 3


async def test_gives_up_after_the_last_attempt():
    call, calls = failing(*[TEMPORARY] * 5)
    with pytest.raises(UpstreamError, match="HTTP 503"):
        await policy(attempts=5).run(call)
    assert len(calls) == 5


@pytest.mark.parametrize("error", [UpstreamError("HTTP 404"), ValueError("a bug")], ids=["permanent", "other"])
async def test_other_errors_are_not_retried(error):
    call, calls = failing(error)
    with pytest.raises(type(error)):
        await policy().run(call)
    assert len(calls) == 1


async def test_waits_use_full_jitter_up_to_a_growing_cap():
    ranges, waits = [], []

    def uniform(low: float, high: float) -> float:
        ranges.append((low, high))
        return high

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    call, _ = failing(*[TEMPORARY] * 6)
    with pytest.raises(UpstreamError):
        await policy(attempts=6, base_delay=0.5, max_delay=3, sleep=sleep, uniform=uniform).run(call)
    assert ranges == [(0, 0.5), (0, 1), (0, 2), (0, 3), (0, 3)]  # from 0, doubling, capped
    assert waits == [0.5, 1, 2, 3, 3]


@pytest.mark.parametrize(
    "answer, temporary",
    [
        (lambda: httpx.Response(429), True),
        (lambda: httpx.Response(500), True),
        (lambda: httpx.Response(503), True),
        (lambda: httpx.ConnectError("refused"), True),
        (lambda: httpx.ReadTimeout("slow"), True),
        (lambda: httpx.Response(400), False),
        (lambda: httpx.Response(404), False),
        (lambda: httpx.UnsupportedProtocol("bad URL"), False),
    ],
    ids=["429", "500", "503", "connect-error", "timeout", "400", "404", "bad-url"],
)
async def test_failures_are_sorted_into_temporary_or_permanent(answer, temporary):
    def handler(request: httpx.Request) -> httpx.Response:
        result = answer()
        if isinstance(result, Exception):
            raise result
        return result

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://source") as http:
        with pytest.raises(UpstreamError) as caught:
            await SourceClient(http).get_document("doc-1")
    assert caught.value.temporary is temporary
