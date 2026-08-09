"""Simple in-memory rate limiting middleware for the FastAPI app.

A hand-rolled fixed-window counter per client IP -- no new dependency
(slowapi isn't installed and isn't otherwise needed for a single-process
API). Not distributed-safe: each worker process keeps its own counters,
which is fine for this project's current single-instance deployment. A
real multi-instance deployment would need a shared store (Redis is
already a planned dependency per docker-compose.yml) instead of this.

Implemented as a raw ASGI middleware rather than a BaseHTTPMiddleware
subclass. BaseHTTPMiddleware pumps *every* response through an anyio
task group and memory stream, which interferes with long-lived streaming
responses -- and GET /live/runs/{id}/events holds an SSE connection open
for the whole 65-125s of a run. Exempting that one path would not have
helped: the wrapping happens regardless of what the middleware decides.
A plain ASGI callable short-circuits over-limit requests and is otherwise
a transparent pass-through of (scope, receive, send).
"""

import time
from threading import Lock

from starlette.responses import JSONResponse

DEFAULT_EXEMPT_PATHS = frozenset({"/health"})

# Prefix-matched exemptions. The SSE stream is one long-lived connection
# that an EventSource reconnects automatically on any network blip --
# charging each reconnect against a 60/minute budget would cut off a
# participant mid-run for doing nothing wrong.
DEFAULT_EXEMPT_PREFIXES = ("/live/runs",)

# Module-level (not per-middleware-instance) state: Starlette builds the
# actual middleware instance lazily and doesn't expose it for easy
# introspection afterward, so tests reset counters via
# reset_rate_limit_state() rather than reaching into the app's middleware
# stack. All requests through TestClient share one pseudo client IP
# ("testclient"), so without an explicit reset between tests, request
# counts would accumulate across the whole test session and eventually
# start returning 429s that have nothing to do with what a given test is
# actually checking.
_counters: dict[str, tuple[int, float]] = {}  # client_ip -> (count, window_start)
_lock = Lock()


def reset_rate_limit_state() -> None:
    """Test-only helper: clear all rate-limit counters."""
    with _lock:
        _counters.clear()


class RateLimitMiddleware:
    """Fixed-window rate limiter: at most `limit` requests per `window_seconds` per client IP."""

    def __init__(
        self,
        app,
        limit: int = 60,
        window_seconds: float = 60.0,
        exempt_paths: frozenset = DEFAULT_EXEMPT_PATHS,
        exempt_prefixes: tuple = DEFAULT_EXEMPT_PREFIXES,
    ):
        self.app = app
        self.limit = limit
        self.window_seconds = window_seconds
        self.exempt_paths = exempt_paths
        self.exempt_prefixes = exempt_prefixes

    def _is_exempt(self, path: str) -> bool:
        return path in self.exempt_paths or path.startswith(self.exempt_prefixes)

    async def __call__(self, scope, receive, send):
        # Lifespan and websocket scopes have no path/client to limit on.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._is_exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        now = time.monotonic()

        with _lock:
            count, window_start = _counters.get(client_ip, (0, now))
            if now - window_start >= self.window_seconds:
                count, window_start = 0, now
            count += 1
            _counters[client_ip] = (count, window_start)
            exceeded = count > self.limit

        if exceeded:
            retry_after = max(0.0, self.window_seconds - (now - window_start))
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
