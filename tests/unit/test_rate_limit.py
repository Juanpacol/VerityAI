"""Unit tests for api/rate_limit.py.

Uses its own throwaway FastAPI app (not the shared verityai.api.rest.app)
with a deliberately low limit, so this test's behavior doesn't depend on
how many requests other tests happen to make.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from verityai.api.rate_limit import RateLimitMiddleware, reset_rate_limit_state


@pytest.fixture(autouse=True)
def _reset():
    reset_rate_limit_state()
    yield
    reset_rate_limit_state()


def make_app(limit=3, window_seconds=60.0, exempt_paths=frozenset({"/health"})):
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware, limit=limit, window_seconds=window_seconds, exempt_paths=exempt_paths
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


class TestRateLimitMiddleware:
    def test_requests_within_limit_succeed(self):
        client = TestClient(make_app(limit=3))
        for _ in range(3):
            assert client.get("/ping").status_code == 200

    def test_request_over_limit_returns_429(self):
        client = TestClient(make_app(limit=3))
        for _ in range(3):
            client.get("/ping")

        response = client.get("/ping")

        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_exempt_path_never_limited(self):
        client = TestClient(make_app(limit=1, exempt_paths=frozenset({"/health"})))
        for _ in range(10):
            assert client.get("/health").status_code == 200

    def test_window_resets_after_expiry(self):
        # Near-zero window: the second request happens well after "expiry"
        # in wall-clock terms, without an actual multi-second test sleep.
        client = TestClient(make_app(limit=1, window_seconds=0.001))
        assert client.get("/ping").status_code == 200
        import time

        time.sleep(0.01)
        assert client.get("/ping").status_code == 200
