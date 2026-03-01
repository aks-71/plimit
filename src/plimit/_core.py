"""Async concurrency limiter — Python port of p-limit."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Awaitable, Callable, Iterable, TypeVar

T = TypeVar("T")


class ClearQueueError(Exception):
    """Raised for pending tasks when ``clear_queue()`` is called with *reject_on_clear* enabled."""


def _validate_concurrency(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise TypeError("Expected `concurrency` to be an integer from 1 and up")
    return value


class Limiter:
    """A concurrency limiter for async functions.

    Create via :func:`p_limit` rather than instantiating directly.
    """

    __slots__ = ("_concurrency", "_reject_on_clear", "_active_count", "_queue")

    def __init__(self, concurrency: int, *, reject_on_clear: bool = False) -> None:
        self._concurrency = _validate_concurrency(concurrency)
        if not isinstance(reject_on_clear, bool):
            raise TypeError("Expected `reject_on_clear` to be a boolean")
        self._reject_on_clear = reject_on_clear
        self._active_count = 0
        # Each queue entry is (future, async_fn, args)
        self._queue: deque[tuple[asyncio.Future[Any], Callable[..., Any], tuple[Any, ...]]] = deque()

    # -- properties -----------------------------------------------------------

    @property
    def active_count(self) -> int:
        """Number of functions currently executing."""
        return self._active_count

    @property
    def pending_count(self) -> int:
        """Number of functions waiting in the queue."""
        return len(self._queue)

    @property
    def concurrency(self) -> int:
        """Get or set the concurrency limit.

        When increased, queued tasks are started up to the new limit.
        """
        return self._concurrency

    @concurrency.setter
    def concurrency(self, value: int) -> None:
        self._concurrency = _validate_concurrency(value)
        # Drain the queue up to the new limit
        self._resume()

    # -- internal -------------------------------------------------------------

    def _resume(self) -> None:
        while self._active_count < self._concurrency and self._queue:
            future, fn, args = self._queue.popleft()
            if future.cancelled():
                continue
            self._active_count += 1
            asyncio.ensure_future(self._worker(future, fn, args))

    async def _worker(self, future: asyncio.Future[Any], fn: Callable[..., Any], args: tuple[Any, ...]) -> None:
        """Run tasks in a loop, picking up the next queued item after each completion."""
        while True:
            try:
                result = fn(*args)
                if asyncio.isfuture(result) or asyncio.iscoroutine(result):
                    result = await result
                future.set_result(result)
            except BaseException as exc:
                if not future.cancelled():
                    future.set_exception(exc)

            # Try to pick up the next task from the queue
            self._active_count -= 1
            if self._active_count < self._concurrency and self._queue:
                future, fn, args = self._queue.popleft()
                if future.cancelled():
                    continue
                self._active_count += 1
            else:
                break

    # -- public API -----------------------------------------------------------

    def __call__(self, fn: Callable[..., T | Awaitable[T]], *args: Any) -> asyncio.Future[T]:
        r"""Schedule *fn(\*args)* respecting the concurrency limit.

        Returns a future that resolves to the return value of *fn(\*args)*.
        The task is enqueued immediately (synchronously), so properties like
        ``active_count`` and ``pending_count`` update before the caller awaits.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        self._queue.append((future, fn, args))
        self._resume()
        return future

    def clear_queue(self) -> None:
        """Discard pending functions that have not started yet.

        If *reject_on_clear* was enabled, the futures of pending calls are
        rejected with :class:`ClearQueueError`.  Otherwise they are silently
        dropped (their returned futures will never resolve).
        """
        if not self._reject_on_clear:
            self._queue.clear()
            return

        while self._queue:
            future, _fn, _args = self._queue.popleft()
            if not future.done():
                future.set_exception(ClearQueueError("Queue was cleared"))

    async def map(self, iterable: Iterable[Any], fn: Callable[..., Any]) -> list[Any]:
        """Process *iterable* through *fn* with limited concurrency.

        *fn* receives ``(item, index)`` for each element. Returns a list of
        results in the original order.
        """
        futures = [self(fn, item, index) for index, item in enumerate(iterable)]
        return list(await asyncio.gather(*futures))


def p_limit(concurrency: int | None = None, *, reject_on_clear: bool = False, **kwargs: Any) -> Limiter:
    """Create a concurrency limiter.

    Parameters
    ----------
    concurrency:
        Maximum number of async functions running at the same time (>= 1).
    reject_on_clear:
        When ``True``, :meth:`Limiter.clear_queue` rejects pending futures
        with :class:`ClearQueueError` instead of silently discarding them.

    Returns
    -------
    Limiter
        A callable limiter instance.

    Examples
    --------
    >>> limit = p_limit(5)
    >>> result = await limit(some_async_fn, arg1, arg2)
    """
    if isinstance(concurrency, dict):
        kwargs.update(concurrency)
        concurrency = kwargs.pop("concurrency", None)
    if concurrency is None:
        concurrency = kwargs.pop("concurrency", None)
    if concurrency is None:
        raise TypeError("Expected `concurrency` to be an integer from 1 and up")
    reject_on_clear = kwargs.pop("reject_on_clear", reject_on_clear)
    return Limiter(concurrency, reject_on_clear=reject_on_clear)


def limit_function(
    fn: Callable[..., Any],
    concurrency: int | None = None,
    *,
    reject_on_clear: bool = False,
    **kwargs: Any,
) -> Callable[..., Any]:
    """Return a concurrency-limited version of *fn*.

    Parameters
    ----------
    fn:
        The async (or sync) function to wrap.
    concurrency:
        Maximum concurrent executions (>= 1).

    Returns
    -------
    Callable
        A wrapper that forwards calls through the limiter.

    Examples
    --------
    >>> limited = limit_function(fetch, concurrency=3)
    >>> result = await limited(url)
    """
    limiter = p_limit(concurrency, reject_on_clear=reject_on_clear, **kwargs)

    def wrapper(*args: Any) -> asyncio.Future[Any]:
        return limiter(fn, *args)

    # Expose limiter properties on the wrapper for introspection
    wrapper.active_count = property(lambda self: limiter.active_count)  # type: ignore[attr-defined]
    wrapper.pending_count = property(lambda self: limiter.pending_count)  # type: ignore[attr-defined]
    wrapper.limiter = limiter  # type: ignore[attr-defined]

    return wrapper
