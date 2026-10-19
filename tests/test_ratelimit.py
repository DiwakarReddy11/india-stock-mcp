"""Rate limiter tests, calling the ASGI middleware directly."""

from india_stock_mcp.ratelimit import RateLimitMiddleware


async def ok_app(scope, receive, send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def request(app, path: str = "/mcp", client_ip: str = "10.0.0.1", forwarded: str | None = None) -> int:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    scope = {"type": "http", "path": path, "headers": headers, "client": (client_ip, 5000)}
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent[0]["status"]


async def test_blocks_after_limit() -> None:
    app = RateLimitMiddleware(ok_app, limit_per_minute=3)
    statuses = [await request(app) for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429]


async def test_health_is_never_limited() -> None:
    app = RateLimitMiddleware(ok_app, limit_per_minute=1)
    statuses = [await request(app, path="/health") for _ in range(5)]
    assert statuses == [200] * 5


async def test_each_client_has_its_own_limit() -> None:
    app = RateLimitMiddleware(ok_app, limit_per_minute=1)
    assert await request(app, client_ip="10.0.0.1") == 200
    assert await request(app, client_ip="10.0.0.1") == 429
    assert await request(app, client_ip="10.0.0.2") == 200


async def test_forged_forwarded_for_does_not_bypass_limit() -> None:
    app = RateLimitMiddleware(ok_app, limit_per_minute=1, trusted_proxy_hops=1)
    assert await request(app, forwarded="1.1.1.1") == 200
    # The client prepends fake addresses; the proxy-added (right-most) one is still 1.1.1.1.
    assert await request(app, forwarded="6.6.6.6, 1.1.1.1") == 429
    assert await request(app, forwarded="7.7.7.7, 1.1.1.1") == 429


async def test_forwarded_for_is_ignored_without_trusted_proxies() -> None:
    app = RateLimitMiddleware(ok_app, limit_per_minute=1, trusted_proxy_hops=0)
    assert await request(app, forwarded="1.1.1.1") == 200
    assert await request(app, forwarded="2.2.2.2") == 429  # same socket peer, header not trusted
