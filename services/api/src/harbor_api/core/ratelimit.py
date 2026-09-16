import time

from redis.asyncio import Redis


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after_s = retry_after_s


async def check_rate_limit(redis: Redis, key: str, *, limit: int, window_s: int = 60) -> None:
    """Fixed-window counter: one INCR plus an EXPIRE on first use.

    Good enough to keep a public demo from being drained; the token-bucket limiter with
    per-tenant budgets arrives with the customer channels in M3.
    """
    window = int(time.time()) // window_s
    redis_key = f"harbor:rl:{key}:{window}"
    count = await redis.incr(redis_key)
    if count == 1:
        await redis.expire(redis_key, window_s)
    if count > limit:
        raise RateLimitExceeded(retry_after_s=window_s - int(time.time()) % window_s)
