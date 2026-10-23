"""Short-lived limits for password login, shared by all production workers."""

import hashlib
import logging
from math import ceil

from fastapi import HTTPException
from redis import Redis, RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)
WINDOW_SECONDS = 15 * 60
ACCOUNT_SOURCE_LIMIT = 5
SOURCE_LIMIT = 50

_CHECK = """
local account = tonumber(redis.call('GET', KEYS[1]) or '0')
local source = tonumber(redis.call('GET', KEYS[2]) or '0')
if account >= tonumber(ARGV[1]) then
    return {1, math.max(1, redis.call('TTL', KEYS[1]))}
end
if source >= tonumber(ARGV[2]) then
    return {2, math.max(1, redis.call('TTL', KEYS[2]))}
end
return {0, 0}
"""

_FAILURE = """
for i = 1, 2 do
    local count = redis.call('INCR', KEYS[i])
    if count == 1 then redis.call('EXPIRE', KEYS[i], ARGV[1]) end
end
return 1
"""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LoginThrottle:
    def __init__(self, username: str, source: str):
        origin = _digest(source)
        self.keys = (
            "auth:failed:account-source:" + _digest(username.strip().casefold() + ":" + origin),
            "auth:failed:source:" + origin,
        )
        self.source = source

    def _client(self) -> Redis:
        return Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)

    def check(self) -> None:
        try:
            with self._client() as client:
                reason, seconds = client.eval(_CHECK, 2, *self.keys, ACCOUNT_SOURCE_LIMIT, SOURCE_LIMIT)
        except RedisError:
            logger.exception("Password login throttle unavailable")
            raise HTTPException(503, "登录服务暂时不可用，请稍后重试") from None
        if reason:
            logger.warning("Password login rejected: reason=%s source=%s", reason, self.source)
            raise HTTPException(
                429,
                f"登录尝试过于频繁，请约 {ceil(seconds / 60)} 分钟后重试",
                headers={"Retry-After": str(seconds)},
            )

    def failed(self) -> None:
        try:
            with self._client() as client:
                client.eval(_FAILURE, 2, *self.keys, WINDOW_SECONDS)
        except RedisError:
            logger.exception("Password login failure could not be counted")
            raise HTTPException(503, "登录服务暂时不可用，请稍后重试") from None

    def succeeded(self) -> None:
        try:
            with self._client() as client:
                client.delete(self.keys[0])
        except RedisError:
            logger.exception("Password login limit could not be cleared")
            raise HTTPException(503, "登录服务暂时不可用，请稍后重试") from None
