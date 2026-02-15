# Easierlit API Reference (v0.2.0)

This document defines method-level contracts for Easierlit v0.2.0.

## 1. Scope and Contract Notation

- Scope: public APIs only.
- Out of scope: private/internal methods prefixed with `_`.
- Runtime behavior is documented from current implementation and tests.

Contract fields used for each method:

- `Signature`: exact callable shape.
- `Purpose`: what the method is for.
- `When to call / When not to call`: usage boundaries.
- `Parameters`: type, defaults, constraints.
- `Returns`: return type and semantics.
- `Raises`: direct and major propagated exceptions.
- `Side effects`: runtime/environment/state changes.
- `Concurrency/worker notes`: threading/process/event-loop notes.
- `Failure modes and fixes`: common failure + recovery.
- `Examples`: one normal and one edge/failure case.

## 2. EasierlitServer

### 2.1 `EasierlitServer.__init__`

- Signature

```python
EasierlitServer(
    client: EasierlitClient,
    host: str = "127.0.0.1",
    port: int = 8000,
    root_path: str = "",
    auth: EasierlitAuthConfig | None = None,
    persistence: EasierlitPersistenceConfig | None = None,
)
```

- Purpose
- Create the server object and store runtime configuration.

- When to call / When not to call
- Call once when assembling your app bootstrap.
- Do not call with `client=None` or non-client objects.

- Parameters
- `client`: required `EasierlitClient`.
- `host`: bind host string.
- `port`: bind port integer.
- `root_path`: Chainlit root path for reverse proxy setups.
- `auth`: optional single-account auth config.
- `persistence`: optional persistence config.

- Returns
- `None` (constructor).

- Raises
- No direct validation exceptions in constructor for host/port.
- Any invalid auth values raise from `EasierlitAuthConfig` before passing.

- Side effects
- If `persistence` is `None`, internally sets default `EasierlitPersistenceConfig()`.

- Concurrency/worker notes
- Safe to construct before worker start.

- Failure modes and fixes
- Misconfigured auth values: validate `EasierlitAuthConfig` inputs first.

- Examples

```python
from easierlit import EasierlitClient, EasierlitServer

client = EasierlitClient(run_func=lambda app: None)
server = EasierlitServer(client=client, host="0.0.0.0", port=8000)
```

```python
# Edge: explicit auth + persistence
from easierlit import EasierlitAuthConfig, EasierlitPersistenceConfig, EasierlitServer

server = EasierlitServer(
    client=client,
    auth=EasierlitAuthConfig(username="admin", password="admin"),
    persistence=EasierlitPersistenceConfig(enabled=True, sqlite_path=".chainlit/easierlit.db"),
)
```

### 2.2 `EasierlitServer.serve`

- Signature

```python
def serve(self) -> None
```

- Purpose
- Bind runtime, start worker, and run Chainlit server (blocking).

- When to call / When not to call
- Call once in your program entrypoint.
- Do not call if you need non-blocking in the same thread.

- Parameters
- None.

- Returns
- `None` after Chainlit exits.

- Raises
- May propagate `WorkerAlreadyRunningError` from `client.run(...)`.
- May propagate `TypeError` in process mode when `run_func` is not picklable.
- May propagate `RunFuncExecutionError` from `client.stop()` on shutdown if worker crashed.
- May propagate Chainlit/runtime exceptions.

- Side effects
- Sets environment variables:
- `CHAINLIT_HOST`
- `CHAINLIT_PORT`
- `CHAINLIT_ROOT_PATH`
- `CHAINLIT_AUTH_COOKIE_NAME=easierlit_access_token`
- `CHAINLIT_AUTH_SECRET` (from `.chainlit/jwt.secret` auto-management)
- Forces Chainlit config:
- `config.run.headless = True`
- `config.ui.default_sidebar_state = "open"`
- Registers a worker crash handler that triggers process shutdown signal.

- Concurrency/worker notes
- This call owns process lifecycle and blocks terminal.

- Failure modes and fixes
- Worker crash: inspect server traceback logs, fix `run_func`, restart.
- Port conflicts: change `port`.

- Examples

```python
server = EasierlitServer(client=client)
server.serve()  # blocking
```

```python
# Edge: graceful operational expectation
# If run_func crashes, server emits logs and exits (fail-fast policy).
```

## 3. EasierlitClient

### 3.1 `EasierlitClient.__init__`

- Signature

```python
EasierlitClient(
    run_func: Callable[[EasierlitApp], Any],
    worker_mode: Literal["thread", "process"] = "thread",
    run_func_mode: Literal["auto", "sync", "async"] = "auto",
)
```

- Purpose
- Store app handler and configure worker execution mode.

- When to call / When not to call
- Call once per logical app runtime.
- Do not pass unsupported `worker_mode`.

- Parameters
- `run_func`: function executed by worker.
- `worker_mode`: `"thread"` or `"process"`.
- `run_func_mode`:
  - `"auto"`: execute sync function directly; await async result automatically.
  - `"sync"`: require non-awaitable return from `run_func`.
  - `"async"`: require awaitable return from `run_func`.

- Returns
- `None` (constructor).

- Raises
- `ValueError` if `worker_mode` is not `"thread"`/`"process"`, or if `run_func_mode` is invalid.

- Side effects
- Initializes worker/error state and runtime handle.

- Concurrency/worker notes
- In `process` mode, `run_func` must be picklable when `run(...)` starts.
- In `"auto"` mode, async `run_func` is supported in both thread/process workers.

- Failure modes and fixes
- Invalid worker mode: use exact string values.
- Invalid `run_func_mode`: use `"auto"`, `"sync"`, or `"async"`.

- Examples

```python
client = EasierlitClient(run_func=my_run_func, worker_mode="thread")
```

```python
# Edge/failure
try:
    EasierlitClient(run_func=my_run_func, worker_mode="async")
except ValueError:
    ...
```

```python
# Edge/failure
try:
    EasierlitClient(run_func=my_run_func, run_func_mode="invalid")
except ValueError:
    ...
```

### 3.2 `EasierlitClient.run`

- Signature

```python
def run(self, app: EasierlitApp) -> None
```

- Purpose
- Start one global worker and execute `run_func(app)`.

- When to call / When not to call
- Call once after creating a fresh `EasierlitApp`.
- Do not call while another worker is still running.

- Parameters
- `app`: communication bridge instance.

- Returns
- `None`.

- Raises
- `WorkerAlreadyRunningError` if worker already active.
- `TypeError` in process mode if `run_func` cannot be pickled.

- Side effects
- Starts daemon thread/process worker.
- Resets prior worker error state.

- Concurrency/worker notes
- Process mode starts an extra monitor thread for crash errors.

- Failure modes and fixes
- Duplicate start: call `stop()` first.
- Process pickling failure: move nested closures/lambdas to top-level functions.

- Examples

```python
app = EasierlitApp()
client.run(app)
```

```python
# Edge/failure
client.run(app)
try:
    client.run(app)
except Exception as exc:
    print(type(exc).__name__)  # WorkerAlreadyRunningError
```

### 3.3 `EasierlitClient.stop`

- Signature

```python
def stop(self, timeout: float = 5.0) -> None
```

- Purpose
- Stop worker, close app bridge, join worker resources, surface worker crash.

- When to call / When not to call
- Call during shutdown.
- Do not ignore raised `RunFuncExecutionError` in production ops.

- Parameters
- `timeout`: join timeout for worker/monitor threads and process.

- Returns
- `None`.

- Raises
- `RunFuncExecutionError` if `run_func` crashed.

- Side effects
- Closes app bridge.
- Joins thread/process, may force terminate process after timeout.
- Clears internal process/app references.

- Concurrency/worker notes
- Safe to call from shutdown path; still may raise crash error by design.

- Failure modes and fixes
- Crash surfaced on stop: inspect traceback text and fix root cause in `run_func`.

- Examples

```python
try:
    client.stop(timeout=5.0)
except Exception as exc:
    print(type(exc).__name__)
```

```python
# Edge: quick forced stop window
client.stop(timeout=0.1)
```

### 3.4 `EasierlitClient.list_threads`

- Signature

```python
def list_threads(
    self,
    first: int = 20,
    cursor: str | None = None,
    search: str | None = None,
    user_identifier: str | None = None,
)
```

- Purpose
- Query paginated thread list from Chainlit data layer.

- When to call / When not to call
- Call when persistence is enabled/data layer exists.
- Do not call in pure no-persistence mode.

- Parameters
- `first`: page size.
- `cursor`: pagination cursor.
- `search`: search query.
- `user_identifier`: optional user scope; resolved to user id first.

- Returns
- Chainlit `PaginatedResponse` with `data` list of thread dicts.
- On SQLite SQLAlchemy path, JSON string tags are normalized to `list[str]` where possible.

- Raises
- `DataPersistenceNotEnabledError` when no data layer.
- `ValueError` if `user_identifier` is provided but user not found.

- Side effects
- None beyond data layer read.

- Concurrency/worker notes
- Uses runtime sync-to-async bridge (`run_coroutine_sync`).

- Failure modes and fixes
- Missing data layer: enable default persistence or configure external DB.
- Unknown user identifier: pass a valid identifier or omit filter.

- Examples

```python
threads = client.list_threads(first=10)
for t in threads.data:
    print(t["id"], t.get("name"))
```

```python
# Edge/failure
try:
    client.list_threads(user_identifier="missing-user")
except ValueError as exc:
    print(exc)
```

### 3.5 `EasierlitClient.get_thread`

- Signature

```python
def get_thread(self, thread_id: str) -> dict
```

- Purpose
- Fetch a single thread by id.

- When to call / When not to call
- Call for thread lookup/detail view paths.
- Do not call when persistence is disabled.

- Parameters
- `thread_id`: target thread id.

- Returns
- Thread dict.
- SQLite path normalizes JSON tags string to list when parseable.

- Raises
- `DataPersistenceNotEnabledError` when no data layer.
- `ValueError` if thread not found.

- Side effects
- None beyond data layer read.

- Concurrency/worker notes
- Safe from worker code.

- Failure modes and fixes
- Not found: confirm thread id and ownership/login context.

- Examples

```python
thread = client.get_thread("thread-1")
print(thread["id"], thread.get("tags"))
```

```python
# Edge/failure
try:
    client.get_thread("not-exists")
except ValueError:
    ...
```

### 3.6 `EasierlitClient.update_thread`

- Signature

```python
def update_thread(
    self,
    thread_id: str,
    name: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> None
```

- Purpose
- Upsert/update thread metadata.

- When to call / When not to call
- Call to rename/create/update thread-level metadata/tags.
- Do not call without persistence.

- Parameters
- `thread_id`: target thread id.
- `name`: optional display name.
- `metadata`: optional metadata dict.
- `tags`: optional tag list.

- Returns
- `None`.

- Raises
- `DataPersistenceNotEnabledError` when no data layer.
- Data-layer-specific exceptions may propagate.

- Side effects
- If auth is configured, auto-resolves/creates owner user and stores `user_id`.
- For SQLite SQLAlchemyDataLayer, serializes `tags` list to JSON string before update.

- Concurrency/worker notes
- Owner resolution calls `get_user` and may call `create_user` once.

- Failure modes and fixes
- SQLite tag binding issues: keep `tags` as list and let Easierlit normalize.
- Ownership missing: ensure `auth` is configured on server.

- Examples

```python
client.update_thread(
    thread_id="thread-1",
    name="Renamed",
    metadata={"source": "run_func"},
    tags=["demo", "active"],
)
```

```python
# Edge: create/upsert style call with only id
client.update_thread(thread_id="new-thread-id")
```

### 3.7 `EasierlitClient.delete_thread`

- Signature

```python
def delete_thread(self, thread_id: str) -> None
```

- Purpose
- Delete thread by id in data layer.

- When to call / When not to call
- Call for explicit thread cleanup paths.
- Do not call without persistence.

- Parameters
- `thread_id`: target thread id.

- Returns
- `None`.

- Raises
- `DataPersistenceNotEnabledError` when no data layer.
- Data-layer-specific errors may propagate.

- Side effects
- Removes persisted thread record.

- Concurrency/worker notes
- Safe from run worker commands.

- Failure modes and fixes
- Not deleted due ACL/backend rules: verify auth and backend constraints.

- Examples

```python
client.delete_thread("thread-1")
```

```python
# Edge/failure
try:
    client.delete_thread("missing")
except Exception as exc:
    print(exc)
```

### 3.8 `EasierlitClient.add_message`

- Signature

```python
def add_message(
    self,
    thread_id: str,
    content: str,
    author: str = "Assistant",
    metadata: dict | None = None,
) -> str
```

- Purpose
- Create one assistant message step and return generated message id.

- When to call / When not to call
- Call from worker logic when writing assistant output outside `app.send`.
- Do not call from contexts where no active session and no data layer exists.

- Parameters
- `thread_id`: target thread.
- `content`: message text.
- `author`: assistant author label.
- `metadata`: optional metadata dict.

- Returns
- Generated `message_id` (UUID string).

- Raises
- `ThreadSessionNotActiveError` when no active session and no data layer fallback.
- `RuntimeError` if called from Chainlit event loop in sync wait path.
- Data-layer/runtime exceptions may propagate.

- Side effects
- If session active: realtime UI send.
- Else: fallback persistence create_step with `type="assistant_message"`.

- Concurrency/worker notes
- Synchronously waits on runtime loop via `run_coroutine_sync`.

- Failure modes and fixes
- No session/data layer: enable persistence or call only on active threads.
- Event-loop misuse: call from worker thread/process, not Chainlit callback loop.

- Examples

```python
msg_id = client.add_message(
    thread_id=incoming.thread_id,
    content="Hello from worker",
    author="Bot",
)
```

```python
# Edge/failure
try:
    client.add_message("thread-x", "hello")
except Exception as exc:
    print(type(exc).__name__)
```

### 3.9 `EasierlitClient.update_message`

- Signature

```python
def update_message(
    self,
    thread_id: str,
    message_id: str,
    content: str,
    metadata: dict | None = None,
) -> None
```

- Purpose
- Update an existing message/step content.

- When to call / When not to call
- Call when you already know `message_id` to revise content.
- Do not call for non-existent message ids unless backend upsert behavior is acceptable.

- Parameters
- `thread_id`: target thread.
- `message_id`: message id to update.
- `content`: updated content.
- `metadata`: optional metadata update.

- Returns
- `None`.

- Raises
- `ThreadSessionNotActiveError` when no active session and no data layer.
- `RuntimeError` in sync wait/event-loop misuse.
- Data-layer/runtime exceptions may propagate.

- Side effects
- Realtime update or fallback `update_step` persistence.

- Concurrency/worker notes
- Same sync bridge behavior as `add_message`.

- Failure modes and fixes
- Unknown message id behavior depends on backend; validate ids in your app layer.

- Examples

```python
client.update_message(thread_id="thread-1", message_id=msg_id, content="Updated")
```

```python
# Edge/failure
try:
    client.update_message("thread-1", "missing-id", "Updated")
except Exception as exc:
    print(exc)
```

### 3.10 `EasierlitClient.delete_message`

- Signature

```python
def delete_message(self, thread_id: str, message_id: str) -> None
```

- Purpose
- Delete a message/step by id.

- When to call / When not to call
- Call when message removal is required.
- Do not call with unknown ids if strict behavior is required.

- Parameters
- `thread_id`: target thread.
- `message_id`: message id to remove.

- Returns
- `None`.

- Raises
- `ThreadSessionNotActiveError` when no active session and no data layer.
- `RuntimeError` in sync wait/event-loop misuse.
- Data-layer/runtime exceptions may propagate.

- Side effects
- Realtime remove or fallback `delete_step` persistence.

- Concurrency/worker notes
- Same sync bridge behavior as other message CRUD methods.

- Failure modes and fixes
- Backend may silently ignore missing step ids; enforce app-level checks if required.

- Examples

```python
client.delete_message(thread_id="thread-1", message_id=msg_id)
```

```python
# Edge/failure
try:
    client.delete_message("thread-1", "missing-id")
except Exception as exc:
    print(exc)
```

### 3.11 `EasierlitClient.set_worker_crash_handler` (Advanced)

- Signature

```python
def set_worker_crash_handler(self, handler: Callable[[str], None] | None) -> None
```

- Purpose
- Register/unregister callback invoked with worker traceback text on crash.

- When to call / When not to call
- Call in advanced operational setups.
- Do not rely on it as a replacement for normal exception handling in `run_func`.

- Parameters
- `handler`: callable receiving traceback string, or `None` to clear.

- Returns
- `None`.

- Raises
- No direct exceptions expected.

- Side effects
- Replaces previous crash handler atomically.

- Concurrency/worker notes
- Guarded by lock in implementation.
- Handler runs in worker-related error path, so keep handler fast and safe.

- Failure modes and fixes
- Handler exceptions are not intended operational path; keep handler minimal.

- Examples

```python
def on_crash(tb: str) -> None:
    print("Worker crashed:", tb.splitlines()[-1])

client.set_worker_crash_handler(on_crash)
```

```python
# Edge: clear handler
client.set_worker_crash_handler(None)
```

### 3.12 `EasierlitClient.peek_worker_error` (Advanced)

- Signature

```python
def peek_worker_error(self) -> str | None
```

- Purpose
- Read the latest cached worker traceback without consuming it.

- When to call / When not to call
- Call for diagnostics and shutdown summaries.
- Do not treat `None` as health guarantee for all future operations.

- Parameters
- None.

- Returns
- Traceback string or `None`.

- Raises
- No direct exceptions expected.

- Side effects
- None.

- Concurrency/worker notes
- Lock-protected read.

- Failure modes and fixes
- `None` while worker already gone can still happen before error propagation; call `stop()` to finalize state.

- Examples

```python
err = client.peek_worker_error()
if err:
    print(err)
```

```python
# Edge: after clean run
assert client.peek_worker_error() is None
```

## 4. EasierlitApp

### 4.1 `EasierlitApp.recv`

- Signature

```python
def recv(self, timeout: float | None = None) -> IncomingMessage
```

- Purpose
- Receive next inbound user message from queue.

- When to call / When not to call
- Call in worker loop.
- Do not call after app is closed.

- Parameters
- `timeout`: seconds to wait; `None` blocks indefinitely.

- Returns
- `IncomingMessage`.

- Raises
- `TimeoutError` when timed wait expires.
- `AppClosedError` when app is closed or close sentinel received.

- Side effects
- Dequeues one inbound item.

- Concurrency/worker notes
- Process-safe queue operation.

- Failure modes and fixes
- Frequent timeouts: expected in idle loops; continue loop.
- Closed app: break loop cleanly.

- Examples

```python
try:
    incoming = app.recv(timeout=1.0)
except TimeoutError:
    pass
```

```python
# Edge/failure
try:
    app.recv(timeout=0.1)
except Exception as exc:
    print(type(exc).__name__)  # TimeoutError or AppClosedError
```

### 4.2 `EasierlitApp.arecv`

- Signature

```python
async def arecv(self, timeout: float | None = None) -> IncomingMessage
```

- Purpose
- Async variant of `recv()` for async `run_func`.

- When to call / When not to call
- Call inside async worker loop.
- Do not call after app is closed.

- Parameters
- `timeout`: seconds to wait; `None` blocks indefinitely.

- Returns
- `IncomingMessage`.

- Raises
- `TimeoutError` when timed wait expires.
- `AppClosedError` when app is closed or close sentinel received.

- Side effects
- Dequeues one inbound item.

- Concurrency/worker notes
- Bridges blocking queue get through `asyncio.to_thread`.

- Failure modes and fixes
- Frequent timeouts: expected in idle loops; continue loop.
- Closed app: break loop cleanly.

- Examples

```python
incoming = await app.arecv()
```

```python
# Edge/failure
try:
    await app.arecv(timeout=0.1)
except Exception as exc:
    print(type(exc).__name__)  # TimeoutError or AppClosedError
```

### 4.3 `EasierlitApp.send`

- Signature

```python
def send(
    self,
    thread_id: str,
    content: str,
    author: str = "Assistant",
    metadata: dict | None = None,
) -> str
```

- Purpose
- Enqueue outgoing send command and return generated message id.

- When to call / When not to call
- Call from `run_func` for assistant outputs.
- Do not call after `close()`.

- Parameters
- `thread_id`: target thread.
- `content`: message content.
- `author`: message author.
- `metadata`: optional dict.

- Returns
- Generated `message_id` string.

- Raises
- `AppClosedError` when app is closed.
- `TypeError` when outgoing command is not picklable (for process safety).

- Side effects
- Enqueues `OutgoingCommand(command="send", ...)`.

- Concurrency/worker notes
- Queue is process-safe.

- Failure modes and fixes
- Pickle errors: keep metadata simple JSON-like objects.

- Examples

```python
msg_id = app.send(thread_id=incoming.thread_id, content="hello", author="Bot")
```

```python
# Edge/failure: unpicklable metadata in process-safe queue
try:
    app.send("thread-1", "x", metadata={"bad": lambda x: x})
except TypeError:
    ...
```

### 4.4 `EasierlitApp.update_message`

- Signature

```python
def update_message(
    self,
    thread_id: str,
    message_id: str,
    content: str,
    metadata: dict | None = None,
) -> None
```

- Purpose
- Enqueue outgoing update command.

- When to call / When not to call
- Call when modifying a known message id.
- Do not call after `close()`.

- Parameters
- `thread_id`, `message_id`, `content`, `metadata`.

- Returns
- `None`.

- Raises
- `AppClosedError`, `TypeError` (same constraints as `send`).

- Side effects
- Enqueues `OutgoingCommand(command="update", ...)`.

- Concurrency/worker notes
- Process-safe queue path.

- Failure modes and fixes
- Unknown message ids depend on downstream backend semantics.

- Examples

```python
app.update_message("thread-1", msg_id, "new content")
```

```python
# Edge/failure
app.close()
try:
    app.update_message("thread-1", msg_id, "x")
except AppClosedError:
    ...
```

### 4.5 `EasierlitApp.delete_message`

- Signature

```python
def delete_message(self, thread_id: str, message_id: str) -> None
```

- Purpose
- Enqueue outgoing delete command.

- When to call / When not to call
- Call for message removal.
- Do not call after `close()`.

- Parameters
- `thread_id`, `message_id`.

- Returns
- `None`.

- Raises
- `AppClosedError`, `TypeError`.

- Side effects
- Enqueues `OutgoingCommand(command="delete", ...)`.

- Concurrency/worker notes
- Process-safe queue path.

- Failure modes and fixes
- Missing message id behavior depends on downstream backend.

- Examples

```python
app.delete_message("thread-1", msg_id)
```

```python
# Edge/failure
app.close()
try:
    app.delete_message("thread-1", msg_id)
except AppClosedError:
    ...
```

### 4.6 `EasierlitApp.close`

- Signature

```python
def close(self) -> None
```

- Purpose
- Close bridge and notify queues to terminate dispatcher/recv loops.

- When to call / When not to call
- Call during normal shutdown.
- Safe to call multiple times.

- Parameters
- None.

- Returns
- `None`.

- Raises
- No intentional public exceptions.

- Side effects
- Sets closed flag.
- Pushes inbound sentinel `None`.
- Pushes outbound `OutgoingCommand(command="close")`.

- Concurrency/worker notes
- Idempotent shutdown signal.

- Failure modes and fixes
- If loops continue running, ensure they handle `AppClosedError` or close command properly.

- Examples

```python
app.close()
```

```python
# Edge: idempotent close
app.close()
app.close()
```

### 4.7 `EasierlitApp.is_closed`

- Signature

```python
def is_closed(self) -> bool
```

- Purpose
- Check if app bridge has been closed.

- When to call / When not to call
- Call in loops/polling logic.

- Parameters
- None.

- Returns
- `True` if closed, else `False`.

- Raises
- None.

- Side effects
- None.

- Concurrency/worker notes
- Reads multiprocessing event state.

- Failure modes and fixes
- None.

- Examples

```python
if app.is_closed():
    return
```

```python
# Edge
app.close()
assert app.is_closed() is True
```

## 5. Config and Data Models

### 5.1 `EasierlitAuthConfig`

- Signature

```python
EasierlitAuthConfig(
    username: str,
    password: str,
    identifier: str | None = None,
    metadata: dict[str, Any] | None = None,
)
```

- Contract
- `username` and `password` must be non-empty, non-whitespace.
- `identifier=None` means runtime uses `username` as identifier.
- `metadata=None` becomes `{}` at auth callback/runtime usage time.

- Raises
- `ValueError` for empty/whitespace username or password.
- `TypeError` for unsupported removed keyword arguments.

### 5.2 `EasierlitPersistenceConfig`

- Signature

```python
EasierlitPersistenceConfig(
    enabled: bool = True,
    sqlite_path: str = ".chainlit/easierlit.db",
)
```

- Contract
- Controls whether default SQLite data layer bootstrap is allowed.
- If external data layer env/config exists, Easierlit does not override it.

### 5.3 `IncomingMessage`

- Signature

```python
IncomingMessage(
    thread_id: str,
    session_id: str,
    message_id: str,
    content: str,
    author: str,
    created_at: str | None = None,
    metadata: dict[str, Any] = {},
)
```

- Contract
- Produced by Chainlit callback pipeline and consumed by `app.recv()`/`app.arecv()`.

### 5.4 `OutgoingCommand`

- Signature

```python
OutgoingCommand(
    command: Literal["send", "update", "delete", "close"],
    thread_id: str | None = None,
    message_id: str | None = None,
    content: str | None = None,
    author: str = "Assistant",
    metadata: dict[str, Any] = {},
)
```

- Contract
- Internal command envelope for app->runtime dispatch.
- Publicly exported but typically not manually instantiated by users.

## 6. Exception Matrix and Troubleshooting

| Exception | Typical Trigger | Primary Fix |
| --- | --- | --- |
| `ValueError` | Invalid `worker_mode`/`run_func_mode`, missing user, missing thread, invalid auth fields | Correct inputs, check identifiers/thread ids |
| `WorkerAlreadyRunningError` | `client.run()` called while worker alive | Call `client.stop()` first |
| `RunFuncExecutionError` | Worker crashed and surfaced on `stop()` | Inspect traceback (`peek_worker_error()`), fix `run_func` |
| `DataPersistenceNotEnabledError` | Thread CRUD called without data layer | Enable persistence or configure external data layer |
| `ThreadSessionNotActiveError` | Message CRUD with no active session and no data layer | Use active session or enable persistence fallback |
| `AppClosedError` | Using `EasierlitApp` after close | Stop loop and restart app/server lifecycle |
| `TimeoutError` | `app.recv(timeout=...)`/`app.arecv(timeout=...)` expires | Treat as idle tick and continue loop |
| `TypeError` | Process-mode pickling failure (`run_func` or payload) | Use picklable top-level functions and JSON-like payloads |
| `RuntimeError` | Sync wait attempted from Chainlit event loop | Call client message methods from worker context |

## 7. Chainlit Message vs Tool-call Mapping

Chainlit step categories:

- Message steps: `user_message`, `assistant_message`, `system_message`
- Tool/run family: `tool`, `run`, `llm`, `embedding`, `retrieval`, `rerank`, `undefined`

Easierlit v0.2.0 mapping:

- `app.recv()` and `app.arecv()` ingest user-message flow.
- `app.send()` and `client.add_message()` emit assistant-message flow.
- No dedicated public API for creating tool-call steps.

UI note: Chainlit `ui.cot` values are `full`, `tool_call`, `hidden`.

## 8. Method-to-Example Index

| Method | Primary Example |
| --- | --- |
| `EasierlitServer.__init__`, `serve` | `examples/minimal.py`, `examples/custom_auth.py` |
| `EasierlitClient.run`, `stop` | `examples/minimal.py` |
| `EasierlitClient.list_threads`, `get_thread`, `update_thread`, `delete_thread` | `examples/thread_crud.py`, `examples/thread_create_in_run_func.py` |
| `EasierlitClient.add_message`, `update_message`, `delete_message` | `examples/thread_create_in_run_func.py` |
| `EasierlitApp.recv`, `arecv`, `send` | `examples/minimal.py`, `tests/test_app_queue.py` |
| `EasierlitApp.update_message`, `delete_message` | `tests/test_app_queue.py` |
| `set_worker_crash_handler`, `peek_worker_error` | `tests/test_client_worker.py`, `src/easierlit/server.py` |
