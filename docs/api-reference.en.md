# Easierlit API Reference (v0.3.0)

## 1. Scope and Contract Notes

- Version target: `0.3.0`
- Runtime core: Chainlit (`chainlit>=2.9,<3`)
- This document describes public APIs that are currently supported.
- `EasierlitClient` is thread-worker only (`worker_mode="thread"`).
- `EasierlitApp` is the primary runtime API for message and thread CRUD.

## 2. EasierlitServer

### 2.1 `EasierlitServer.__init__`

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

Parameters:

- `client`: required `EasierlitClient` instance.
- `host`, `port`, `root_path`: Chainlit server binding/runtime path.
- `auth`: optional auth bootstrap config.
- `persistence`: optional persistence config. Defaults to internal SQLite bootstrap behavior.

### 2.2 `EasierlitServer.serve`

```python
serve() -> None
```

Behavior:

- Binds runtime (`client`, `app`, auth, persistence).
- Starts worker via `client.run(app)`.
- Starts Chainlit in headless mode.
- Forces sidebar default state to `open`.
- On shutdown, calls `client.stop()` and unbinds runtime.
- Uses fail-fast policy on worker crash.

May raise:

- `WorkerAlreadyRunningError` from `client.run(...)`.
- `RunFuncExecutionError` from `client.stop(...)` during shutdown when worker crashed.

## 3. EasierlitClient

### 3.1 `EasierlitClient.__init__`

```python
EasierlitClient(
    run_func: Callable[[EasierlitApp], Any],
    worker_mode: Literal["thread"] = "thread",
    run_func_mode: Literal["auto", "sync", "async"] = "auto",
)
```

Parameters:

- `run_func`: user worker entrypoint.
- `worker_mode`: only `"thread"` is supported.
- `run_func_mode`:
- `"auto"`: execute sync or async based on returned object.
- `"sync"`: requires non-awaitable return.
- `"async"`: requires awaitable return.

Raises:

- `ValueError` for invalid `worker_mode`/`run_func_mode`.

### 3.2 `EasierlitClient.run`

```python
run(app: EasierlitApp) -> None
```

Behavior:

- Starts one daemon thread worker.
- Invokes `run_func(app)`.
- For uncaught worker exceptions, records traceback and closes app.

Raises:

- `WorkerAlreadyRunningError` when called while worker is alive.

### 3.3 `EasierlitClient.stop`

```python
stop(timeout: float = 5.0) -> None
```

Behavior:

- Closes app.
- Joins worker thread up to `timeout`.
- Re-raises worker failure as `RunFuncExecutionError`.

### 3.4 `EasierlitClient.set_worker_crash_handler` (advanced)

```python
set_worker_crash_handler(handler: Callable[[str], None] | None) -> None
```

- Registers/unregisters callback invoked with full traceback text when worker crashes.

### 3.5 `EasierlitClient.peek_worker_error` (advanced)

```python
peek_worker_error() -> str | None
```

- Returns recorded worker traceback text if present.

## 4. EasierlitApp

### 4.1 `EasierlitApp.recv`

```python
recv(timeout: float | None = None) -> IncomingMessage
```

Behavior:

- Blocks until incoming user message exists.
- Raises `TimeoutError` if timeout elapses.
- Raises `AppClosedError` if app is already closed.

### 4.2 `EasierlitApp.arecv`

```python
arecv(timeout: float | None = None) -> IncomingMessage
```

- Async variant of `recv`.
- Same timeout/close semantics.

### 4.3 `EasierlitApp.send`

```python
send(
    thread_id: str,
    content: str,
    author: str = "Assistant",
    metadata: dict | None = None,
) -> str
```

Behavior:

- Enqueues outgoing `send` command.
- Returns generated `message_id`.
- Command is later applied by runtime dispatcher.

### 4.4 `EasierlitApp.add_message`

```python
add_message(
    thread_id: str,
    content: str,
    author: str = "Assistant",
    metadata: dict | None = None,
) -> str
```

- Alias of `send` with same queue-based behavior.

### 4.5 `EasierlitApp.update_message`

```python
update_message(
    thread_id: str,
    message_id: str,
    content: str,
    metadata: dict | None = None,
) -> None
```

- Enqueues outgoing `update` command.

### 4.6 `EasierlitApp.delete_message`

```python
delete_message(thread_id: str, message_id: str) -> None
```

- Enqueues outgoing `delete` command.

Message-command execution model:

1. Runtime resolves active websocket session for target thread.
2. If session exists, command applies in realtime context.
3. If session is missing and data layer exists, fallback applies via data layer with internal HTTP context.
4. If both are missing, command application raises `ThreadSessionNotActiveError`.

### 4.7 `EasierlitApp.list_threads`

```python
list_threads(
    first: int = 20,
    cursor: str | None = None,
    search: str | None = None,
    user_identifier: str | None = None,
)
```

Behavior:

- Reads thread list from data layer.
- With `user_identifier`, resolves user and filters by user id.
- SQLite SQLAlchemyDataLayer tags are normalized from JSON string to list.

Raises:

- `DataPersistenceNotEnabledError` when no data layer is configured.
- `ValueError` when `user_identifier` is not found.

### 4.8 `EasierlitApp.get_thread`

```python
get_thread(thread_id: str) -> dict
```

- Returns thread dict.
- Normalizes SQLite tags format.
- Raises `ValueError` if thread does not exist.

### 4.9 `EasierlitApp.new_thread`

```python
new_thread(
    thread_id: str,
    name: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> None
```

Behavior:

- Creates a thread only when target thread id does not exist.
- With auth configured, auto-resolves/creates owner user and writes `user_id`.
- SQLite SQLAlchemyDataLayer stores `tags` as JSON string.

Raises:

- `ValueError` if thread already exists.

### 4.10 `EasierlitApp.update_thread`

```python
update_thread(
    thread_id: str,
    name: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> None
```

Behavior:

- Updates a thread only when target thread id already exists.
- With auth configured, auto-resolves/creates owner user and writes `user_id`.
- SQLite SQLAlchemyDataLayer stores `tags` as JSON string.

Raises:

- `ValueError` if thread does not exist.

### 4.11 `EasierlitApp.delete_thread`

```python
delete_thread(thread_id: str) -> None
```

- Deletes thread via data layer.

### 4.12 `EasierlitApp.close`

```python
close() -> None
```

Behavior:

- Marks app closed.
- Unblocks `recv/arecv` waiters.
- Enqueues `close` outgoing command for dispatcher shutdown.

### 4.13 `EasierlitApp.is_closed`

```python
is_closed() -> bool
```

- Returns whether app is closed.

## 5. Config and Data Models

### 5.1 `EasierlitAuthConfig`

```python
EasierlitAuthConfig(
    username: str,
    password: str,
    identifier: str | None = None,
    metadata: dict[str, Any] | None = None,
)
```

- `username` and `password` must be non-empty.

### 5.2 `EasierlitPersistenceConfig`

```python
EasierlitPersistenceConfig(
    enabled: bool = True,
    sqlite_path: str = ".chainlit/easierlit.db",
)
```

### 5.3 `IncomingMessage`

```python
IncomingMessage(
    thread_id: str,
    session_id: str,
    message_id: str,
    content: str,
    author: str,
    created_at: str | None = None,
    metadata: dict | None = None,
)
```

### 5.4 `OutgoingCommand`

```python
OutgoingCommand(
    command: Literal["send", "update", "delete", "close"],
    thread_id: str | None = None,
    message_id: str | None = None,
    content: str | None = None,
    author: str = "Assistant",
    metadata: dict | None = None,
)
```

## 6. Exception Matrix and Troubleshooting

| Exception | Typical trigger | Action |
|---|---|---|
| `AppClosedError` | `recv()` after app closed, or enqueue/send on closed app | Stop loop and exit worker gracefully |
| `WorkerAlreadyRunningError` | `client.run()` called while worker alive | Call `client.stop()` first |
| `RunFuncExecutionError` | Worker raised uncaught error | Inspect traceback, fix run_func logic |
| `DataPersistenceNotEnabledError` | Thread CRUD without configured data layer | Enable persistence or register data layer |
| `ThreadSessionNotActiveError` | Applying message command without active session and without data layer | Ensure session is active or configure persistence fallback |
| `ValueError` | Invalid worker mode/run_func mode, missing user/thread | Validate inputs and identifiers |

## 7. Chainlit Message vs Tool-call Mapping

- Incoming `app.recv/arecv` maps to user-message flow.
- Outgoing `app.send/add_message` maps to assistant-message flow.
- Easierlit public API does not expose dedicated tool-call step creation.

## 8. Method-to-Example Index

| Method group | Example |
|---|---|
| `EasierlitClient.run`, `stop` | `examples/minimal.py` |
| `EasierlitApp.list_threads`, `get_thread`, `new_thread`, `update_thread`, `delete_thread` | `examples/thread_crud.py`, `examples/thread_create_in_run_func.py` |
| `EasierlitApp.send`, `add_message`, `update_message`, `delete_message` | `examples/minimal.py`, `examples/thread_create_in_run_func.py` |
| Auth + persistence configs | `examples/custom_auth.py` |
