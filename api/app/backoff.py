"""Quick retries inside a run: exponential backoff with full jitter."""
import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .clients import UpstreamError


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int
    base_delay: float
    max_delay: float
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    uniform: Callable[[float, float], float] = random.uniform

    def delay(self, retry: int) -> float:
        """The wait before retry `retry` (1 = the second attempt)."""
        return self.uniform(0, min(self.max_delay, self.base_delay * 2 ** (retry - 1)))

    async def run[T](self, call: Callable[[], Awaitable[T]]) -> T:
        """Runs `call`, repeating it after temporary upstream failures, up to `attempts` times."""
        for retry in range(1, self.attempts):
            try:
                return await call()
            except UpstreamError as exc:
                if not exc.temporary:
                    raise
            await self.sleep(self.delay(retry))
        return await call()
