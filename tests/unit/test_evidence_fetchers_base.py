"""Unit tests for evidence/fetchers/base.py -- RateLimiter and Checkpoint.

Both are driven with fake clocks/sleeps or a tmp_path filesystem, never
real timing or real I/O beyond the throwaway test directory.
"""

from verityai.evidence.fetchers.base import Checkpoint, FetchResult, RateLimiter


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


class SleepRecorder:
    def __init__(self):
        self.calls: list = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class TestRateLimiter:
    def test_first_call_never_sleeps(self):
        clock = FakeClock()
        sleeper = SleepRecorder()
        limiter = RateLimiter(3.0, sleep_fn=sleeper, time_fn=clock)

        limiter.wait()

        assert sleeper.calls == []

    def test_call_before_interval_elapsed_sleeps_for_remainder(self):
        clock = FakeClock()
        sleeper = SleepRecorder()
        limiter = RateLimiter(3.0, sleep_fn=sleeper, time_fn=clock)

        limiter.wait()
        clock.now = 1.0
        limiter.wait()

        assert sleeper.calls == [2.0]

    def test_call_after_interval_elapsed_does_not_sleep(self):
        clock = FakeClock()
        sleeper = SleepRecorder()
        limiter = RateLimiter(3.0, sleep_fn=sleeper, time_fn=clock)

        limiter.wait()
        clock.now = 10.0
        limiter.wait()

        assert sleeper.calls == []

    def test_three_calls_only_second_sleeps(self):
        clock = FakeClock()
        sleeper = SleepRecorder()
        limiter = RateLimiter(3.0, sleep_fn=sleeper, time_fn=clock)

        limiter.wait()
        clock.now = 1.0
        limiter.wait()
        clock.now = 10.0
        limiter.wait()

        assert sleeper.calls == [2.0]


class TestCheckpoint:
    def test_fresh_checkpoint_nothing_done(self, tmp_path):
        checkpoint = Checkpoint(tmp_path / "arxiv.json")
        assert checkpoint.is_done("query a") is False

    def test_mark_done_persists(self, tmp_path):
        checkpoint = Checkpoint(tmp_path / "arxiv.json")
        checkpoint.mark_done("query a")

        assert checkpoint.is_done("query a") is True
        assert checkpoint.is_done("query b") is False

    def test_mark_done_is_idempotent(self, tmp_path):
        checkpoint = Checkpoint(tmp_path / "arxiv.json")
        checkpoint.mark_done("query a")
        checkpoint.mark_done("query a")

        assert checkpoint.load()["done"].count("query a") == 1

    def test_survives_across_instances(self, tmp_path):
        path = tmp_path / "arxiv.json"
        Checkpoint(path).mark_done("query a")

        resumed = Checkpoint(path)
        assert resumed.is_done("query a") is True


class TestFetchResult:
    def test_defaults_to_empty(self):
        result = FetchResult()
        assert result.records == []
        assert result.errors == []
