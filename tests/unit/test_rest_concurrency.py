"""Tests for api/rest.py's configurable sync-endpoint threadpool (see the
module docstring's "Concurrency model" section)."""

import anyio
import pytest

from verityai.api.rest import _lifespan, app


class TestThreadpoolConfiguration:
    @pytest.mark.anyio
    async def test_lifespan_applies_env_var_threadpool_size(self, monkeypatch):
        monkeypatch.setenv("VERITYAI_THREADPOOL_SIZE", "17")

        async with _lifespan(app):
            limiter = anyio.to_thread.current_default_thread_limiter()
            assert limiter.total_tokens == 17

    @pytest.mark.anyio
    async def test_lifespan_leaves_default_when_env_var_unset(self, monkeypatch):
        monkeypatch.delenv("VERITYAI_THREADPOOL_SIZE", raising=False)
        default_before = anyio.to_thread.current_default_thread_limiter().total_tokens

        async with _lifespan(app):
            limiter = anyio.to_thread.current_default_thread_limiter()
            assert limiter.total_tokens == default_before


@pytest.fixture
def anyio_backend():
    return "asyncio"
