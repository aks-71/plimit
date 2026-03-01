# plimit

> Run multiple async functions with limited concurrency

Python port of the popular Node.js [`p-limit`](https://github.com/sindresorhus/p-limit) package. Uses `asyncio` to run async (and sync) functions with a configurable concurrency limit.

## Install

```
pip install plimit
```

or with [uv](https://docs.astral.sh/uv/):

```
uv add plimit
```

## Usage

```python
import asyncio
from plimit import p_limit

async def main():
    limit = p_limit(2)  # max 2 concurrent tasks

    async def fetch(url):
        # ... your async work
        return url

    results = await asyncio.gather(
        limit(fetch, "https://example.com/1"),
        limit(fetch, "https://example.com/2"),
        limit(fetch, "https://example.com/3"),
    )
    print(results)

asyncio.run(main())
```

## API

### `p_limit(concurrency, *, reject_on_clear=False)`

Returns a `Limiter` instance.

- **`concurrency`** *(int)* — Maximum number of async functions running at the same time. Minimum: `1`.
- **`reject_on_clear`** *(bool)* — When `True`, `clear_queue()` rejects pending futures with `ClearQueueError` instead of silently discarding them. Default: `False`.

### `limit(fn, *args)` → awaitable

Schedule `fn(*args)` to run under the concurrency limit. Returns an awaitable that resolves to the return value of `fn`.

- `fn` can be an async function or a regular function.
- Extra positional arguments are forwarded to `fn`.

```python
result = await limit(my_async_fn, arg1, arg2)
```

### `limit.active_count` → int

Number of functions currently executing.

### `limit.pending_count` → int

Number of functions waiting in the queue.

### `limit.concurrency` → int (get/set)

Get or set the concurrency limit at runtime. When increased, queued tasks start immediately up to the new limit.

```python
limit.concurrency = 10  # increase concurrency dynamically
```

### `limit.clear_queue()`

Discard pending functions that have not started yet.

- If `reject_on_clear=True`, pending futures are rejected with `ClearQueueError`.
- Does **not** cancel functions that are already running.

### `limit.map(iterable, fn)` → awaitable list

Process an iterable through `fn` with limited concurrency. The mapper receives `(item, index)`.

```python
results = await limit.map([1, 2, 3], async_transform)
```

### `limit_function(fn, concurrency, *, reject_on_clear=False)`

Convenience wrapper — returns a new async function that calls `fn` with limited concurrency.

```python
from plimit import limit_function

limited_fetch = limit_function(fetch, concurrency=3)
result = await limited_fetch(url)
```

### `ClearQueueError`

Exception raised for pending tasks when `clear_queue()` is called with `reject_on_clear=True`.

## Comparison with JavaScript `p-limit`

| JavaScript (`p-limit`)          | Python (`plimit`)                     |
|----------------------------------|---------------------------------------|
| `const limit = pLimit(5)`        | `limit = p_limit(5)`                  |
| `await limit(fn, ...args)`       | `await limit(fn, *args)`              |
| `limit.activeCount`              | `limit.active_count`                  |
| `limit.pendingCount`             | `limit.pending_count`                 |
| `limit.concurrency = 10`         | `limit.concurrency = 10`              |
| `limit.clearQueue()`             | `limit.clear_queue()`                 |
| `limit.map(iter, fn)`            | `await limit.map(iter, fn)`           |
| `limitFunction(fn, opts)`        | `limit_function(fn, concurrency=N)`   |
| `AbortError` on clear            | `ClearQueueError` on clear            |

## Requirements

Python 3.10+

## License

MIT
