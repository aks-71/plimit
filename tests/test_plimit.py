"""Comprehensive tests for plimit — ported from p-limit JS test suite."""

from __future__ import annotations

import asyncio
import time

import pytest

from plimit import ClearQueueError, Limiter, limit_function, p_limit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _time_ms() -> float:
    return time.monotonic() * 1000


async def _delay(ms: float) -> None:
    await asyncio.sleep(ms / 1000)


# ---------------------------------------------------------------------------
# Core concurrency tests
# ---------------------------------------------------------------------------


async def test_concurrency_1() -> None:
    """With concurrency=1, tasks run sequentially."""
    input_data = [
        (10, 300),
        (20, 200),
        (30, 100),
    ]
    start = _time_ms()
    limit = p_limit(1)

    async def mapper(value: int, ms: float) -> int:
        await _delay(ms)
        return value

    results = await asyncio.gather(*(limit(mapper, v, ms) for v, ms in input_data))
    elapsed = _time_ms() - start

    assert list(results) == [10, 20, 30]
    assert 550 <= elapsed <= 750, f"Expected ~600ms, got {elapsed:.0f}ms"


async def test_concurrency_n() -> None:
    """Concurrency limit is respected under load."""
    concurrency = 5
    running = 0
    max_running = 0
    limit = p_limit(concurrency)

    async def task() -> None:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        assert running <= concurrency
        await _delay(30)
        running -= 1

    await asyncio.gather(*(limit(task) for _ in range(100)))
    assert max_running == concurrency


async def test_non_coroutine_function() -> None:
    """Sync (non-async) functions are supported."""
    limit = p_limit(1)
    result = await limit(lambda: 42)
    assert result == 42


async def test_continues_after_sync_throw() -> None:
    """Queue keeps processing after a task raises."""
    limit = p_limit(1)
    ran = False

    def thrower() -> None:
        raise RuntimeError("err")

    async def follower() -> None:
        nonlocal ran
        ran = True

    tasks = [limit(thrower), limit(follower)]

    with pytest.raises(RuntimeError):
        await asyncio.gather(*tasks)

    assert ran is True


async def test_accepts_additional_arguments() -> None:
    """Extra positional args are forwarded to fn."""
    limit = p_limit(1)
    sentinel = object()
    result = await limit(lambda a: a, sentinel)
    assert result is sentinel


async def test_does_not_ignore_errors() -> None:
    """Errors propagate to the caller."""
    limit = p_limit(1)
    error = ValueError("🦄")

    async def ok_task(ms: float) -> None:
        await _delay(ms)

    async def bad_task() -> None:
        await _delay(80)
        raise error

    tasks = [limit(ok_task, 30), limit(bad_task), limit(ok_task, 50)]
    with pytest.raises(ValueError, match="🦄"):
        await asyncio.gather(*tasks)


async def test_runs_tasks_asynchronously() -> None:
    """Tasks see values set *after* scheduling but *before* execution."""
    limit = p_limit(3)
    value = 1

    t1 = limit(lambda: 1)
    t2 = limit(lambda: value)

    assert limit.active_count == 2

    value = 2
    results = await asyncio.gather(t1, t2)
    assert list(results) == [1, 2]


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


async def test_active_and_pending_count() -> None:
    limit = p_limit(5)
    assert limit.active_count == 0
    assert limit.pending_count == 0

    running1 = limit(lambda: _delay(200))
    assert limit.active_count == 1
    assert limit.pending_count == 0
    await running1

    assert limit.active_count == 0
    assert limit.pending_count == 0

    immediate = [limit(lambda: _delay(200)) for _ in range(5)]
    delayed = [limit(lambda: _delay(200)) for _ in range(3)]

    assert limit.active_count == 5
    assert limit.pending_count == 3

    await asyncio.gather(*immediate)
    assert limit.active_count == 3
    assert limit.pending_count == 0

    await asyncio.gather(*delayed)
    assert limit.active_count == 0
    assert limit.pending_count == 0


# ---------------------------------------------------------------------------
# clear_queue
# ---------------------------------------------------------------------------


async def test_clear_queue() -> None:
    limit = p_limit(1)
    _running = limit(lambda: _delay(1000))
    _pending = [limit(lambda: _delay(1000)) for _ in range(3)]

    assert limit.pending_count == 3
    limit.clear_queue()
    assert limit.pending_count == 0


async def test_clear_queue_rejects_when_enabled() -> None:
    limit = p_limit(1, reject_on_clear=True)

    running = limit(lambda: _delay(200))
    pending1 = limit(lambda: _delay(10))
    pending2 = limit(lambda: _delay(10))

    assert limit.pending_count == 2
    limit.clear_queue()
    assert limit.pending_count == 0

    await running  # should complete normally

    with pytest.raises(ClearQueueError):
        await pending1

    with pytest.raises(ClearQueueError):
        await pending2


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------


async def test_map_basic() -> None:
    limit = p_limit(1)
    results = await limit.map([1, 2, 3, 4, 5, 6, 7], lambda x, _idx: x + 1)
    assert results == [2, 3, 4, 5, 6, 7, 8]


async def test_map_passes_index_and_preserves_order() -> None:
    limit = p_limit(3)
    inputs = [10, 10, 10, 10, 10]

    async def mapper(value: int, index: int) -> int:
        await _delay((len(inputs) - index) * 5)
        return value + index

    results = await limit.map(inputs, mapper)
    assert results == [10, 11, 12, 13, 14]


async def test_map_accepts_set() -> None:
    limit = p_limit(2)
    results = await limit.map({1, 2, 3, 4}, lambda x, _idx: x * 2)
    assert sorted(results) == [2, 4, 6, 8]


async def test_map_accepts_iterator() -> None:
    limit = p_limit(2)
    results = await limit.map(iter([1, 2, 3, 4]), lambda x, _idx: x * 2)
    assert results == [2, 4, 6, 8]


# ---------------------------------------------------------------------------
# Options / validation
# ---------------------------------------------------------------------------


async def test_throws_on_invalid_concurrency() -> None:
    with pytest.raises(TypeError):
        p_limit(0)
    with pytest.raises(TypeError):
        p_limit(-1)
    with pytest.raises(TypeError):
        p_limit(1.2)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        p_limit(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        p_limit(True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Dynamic concurrency
# ---------------------------------------------------------------------------


async def test_change_concurrency_smaller() -> None:
    limit = p_limit(4)
    running = 0
    log: list[int] = []

    async def task() -> None:
        nonlocal running
        running += 1
        log.append(running)
        await _delay(50)
        running -= 1

    promises = [limit(task) for _ in range(10)]
    await asyncio.sleep(0)
    assert running == 4

    limit.concurrency = 2
    await asyncio.gather(*promises)
    assert log == [1, 2, 3, 4, 2, 2, 2, 2, 2, 2]


async def test_change_concurrency_bigger() -> None:
    limit = p_limit(2)
    running = 0
    log: list[int] = []

    async def task() -> None:
        nonlocal running
        running += 1
        log.append(running)
        await _delay(50)
        running -= 1

    promises = [limit(task) for _ in range(10)]
    await asyncio.sleep(0)
    assert running == 2

    limit.concurrency = 4
    await asyncio.gather(*promises)
    assert log == [1, 2, 3, 4, 4, 4, 4, 4, 4, 4]


# ---------------------------------------------------------------------------
# limit_function
# ---------------------------------------------------------------------------


async def test_limit_function() -> None:
    concurrency = 5
    running = 0
    max_running = 0

    async def work() -> None:
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        assert running <= concurrency
        await _delay(30)
        running -= 1

    limited = limit_function(work, concurrency=concurrency)
    await asyncio.gather(*(limited() for _ in range(100)))
    assert max_running == concurrency


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_shared_context_with_args() -> None:
    """Demonstrates passing a shared mutable context via args."""
    limit = p_limit(1)
    context: dict[str, list[str]] = {"values": []}

    async def add_value(ctx: dict[str, list[str]], value: str) -> int:
        ctx["values"].append(value)
        await _delay(10)
        return len(ctx["values"])

    first = limit(add_value, context, "first")
    second = limit(add_value, context, "second")

    assert limit.active_count == 1
    assert limit.pending_count == 1

    results = await asyncio.gather(first, second)
    assert list(results) == [1, 2]
    assert context["values"] == ["first", "second"]
