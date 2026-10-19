"""Per-client rate limiting for the MCP endpoint (in-memory token bucket)."""

from __future__ import annotations

import json
import math
import time

from cachetools import TTLCache
from starlette.types import ASGIApp, Receive, Scope, Send


class RateLimitMiddleware:
    """Allow each client `limit_per_minute` requests to paths under `protected_prefix`.

    Each client gets a bucket of tokens that refills continuously; a request spends one token.
    State lives in this process's memory, so several server instances each limit separately.
    """

    def __init__(
        self,
        app: ASGIApp,
        limit_per_minute: int,
        trusted_proxy_hops: int = 0,
        protected_prefix: str = "/mcp",
    ) -> None:
        self.app = app
        self.capacity = float(limit_per_minute)
        self.refill_per_second = limit_per_minute / 60
        self.trusted_proxy_hops = trusted_proxy_hops
        self.protected_prefix = protected_prefix
        # client ip -> (tokens left, last update); clients idle for 2 minutes are forgotten.
        self._buckets: TTLCache[str, tuple[float, float]] = TTLCache(maxsize=100_000, ttl=120)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.protected_prefix):
            await self.app(scope, receive, send)  # health checks, lifespan events etc. pass through
            return
        allowed, retry_after = self._take_token(self.client_ip(scope))
        if allowed:
            await self.app(scope, receive, send)
        else:
            await self._reject(send, retry_after)

    def client_ip(self, scope: Scope) -> str:
        """The caller's IP address, looking through `trusted_proxy_hops` load balancers."""
        peer = scope.get("client")
        peer_ip = peer[0] if peer else "unknown"
        if self.trusted_proxy_hops == 0:
            return peer_ip
        headers = dict(scope["headers"])  # ASGI header names are lowercase bytes
        forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        # Each proxy appends the address it received the request from, so the entries we can trust
        # are the last ones. Anything further left was sent by the client and could be forged.
        if len(hops) >= self.trusted_proxy_hops:
            return hops[-self.trusted_proxy_hops]
        return peer_ip

    def _take_token(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_second)
        if tokens >= 1:
            self._buckets[key] = (tokens - 1, now)
            return True, 0
        self._buckets[key] = (tokens, now)
        return False, math.ceil((1 - tokens) / self.refill_per_second)

    @staticmethod
    async def _reject(send: Send, retry_after: int) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32000, "message": f"Rate limit exceeded. Retry in {retry_after} seconds."},
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", str(retry_after).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
