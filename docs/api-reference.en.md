# Easierlit API Reference

## 1. Scope and Contract Notes

- Runtime core: Chainlit (`chainlit>=2.9.6,<3`)
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
    max_outgoing_workers: int = 4,
    auth: EasierlitAuthConfig | None = None,
    persistence: EasierlitPersistenceConfig | None = None,
    discord: EasierlitDiscordConfig | None = None,
)
```

Parameters:

- `client`: required `EasierlitClient` instance.
- `host`, `port`, `root_path`: Chainlit server binding/runtime path.
- `max_outgoing_workers`: outgoing dispatcher lane count. Must be `>= 1`. Default `4`.
- `auth`: auth bootstrap config. If `None`, Easierlit auto-enables auth using:
- `EASIERLIT_AUTH_USERNAME` + `EASIERLIT_AUTH_PASSWORD` when both are present.
- fallback `admin` / `admin` when both are absent (warning log emitted).
- `persistence`: optional persistence config. If `None`, default SQLite bootstrap behavior is enabled.
- `persistence.storage_provider`: optional local storage client override for file/image persistence. Easierlit requires `LocalFileStorageClient`.
- `discord`: optional Discord bot config. Defaults to disabled behavior.

### 2.2 `EasierlitServer.serve`

```python
serve() -> None
```

Behavior:

- Binds runtime (`client`, `app`, auth, persistence).
- Binds runtime outgoing dispatcher lane count from `max_outgoing_workers`.
- Starts worker via `client.run(app)`.
- Starts Chainlit in headless mode.
- Forces sidebar default state to `open`.
- Forces CoT mode to `full`.
- Preserves `CHAINLIT_AUTH_COOKIE_NAME` when already set; otherwise sets deterministic scoped cookie name `easierlit_access_token_<hash>`.
- If `CHAINLIT_AUTH_SECRET` is set but shorter than 32 bytes, replaces it with a secure generated secret for the current run; if missing, resolves secret from `.chainlit/jwt.secret`.
- Sets `UVICORN_WS_PROTOCOL=websockets-sansio` when not already configured.
- Resolves Discord token as `bot_token` first, then `DISCORD_BOT_TOKEN` fallback.
- Runs Discord through Easierlit's own bridge (no runtime monkeypatching of Chainlit Discord handlers).
- Does not clear `DISCORD_BOT_TOKEN` during `serve()`; the env value remains unchanged.
- Suppresses Chainlit built-in Discord autostart while Easierlit is running to avoid duplicate Discord replies.
- Restores previous `CHAINLIT_AUTH_COOKIE_NAME` and `CHAINLIT_AUTH_SECRET` after shutdown.
- On shutdown, calls `client.stop()` and unbinds runtime.
- Uses fail-fast policy on worker crash.

May raise:

- `WorkerAlreadyRunningError` from `client.run(...)`.
- `RunFuncExecutionError` from `client.stop(...)` during shutdown when worker crashed.
- `ValueError` when exactly one of `EASIERLIT_AUTH_USERNAME` and `EASIERLIT_AUTH_PASSWORD` is set.
- `ValueError` when `max_outgoing_workers < 1`.
- `ValueError` when Discord is enabled and no non-empty token is available.

## 3. EasierlitClient

### 3.1 `EasierlitClient.__init__`

```python
EasierlitClient(
    on_message: Callable[[EasierlitApp, IncomingMessage], Any],
    run_funcs: list[Callable[[EasierlitApp], Any]] | None = None,
    worker_mode: Literal["thread"] = "thread",
    run_func_mode: Literal["auto", "sync", "async"] = "auto",
    max_message_workers: int = 64,
)
```

Parameters:

- `on_message`: required incoming message handler, sync or async.
- `run_funcs`: optional list of background worker entrypoints.
- `worker_mode`: only `"thread"` is supported.
- `run_func_mode`:
- `"auto"`: execute sync or async based on returned object.
- `"sync"`: requires non-awaitable return.
- `"async"`: requires awaitable return.
- `max_message_workers`: global maximum concurrent on_message workers.

Raises:

- `ValueError` for invalid `worker_mode`/`run_func_mode`, invalid `run_funcs`, or invalid `max_message_workers`.
- `TypeError` when `on_message` is not callable.
- `TypeError` when any `run_funcs` item is not callable.

### 3.2 `EasierlitClient.run`

```python
run(app: EasierlitApp) -> None
```

Behavior:

- Enables message dispatch via `dispatch_incoming(...)`.
- Runs one daemon thread per incoming message.
- Serializes by `thread_id` and executes different thread ids in parallel (up to `max_message_workers`).
- Async awaitables are isolated by role:
- `run_func` awaitables use one dedicated internal event-loop runner.
- `on_message` awaitables use a thread-aware runner pool sized as `min(max_message_workers, 8)`.
- Same `thread_id` maps to the same `on_message` runner lane.
- CPU-bound Python handlers still contend on GIL; this optimization primarily improves awaitable/I/O-heavy paths.
- Starts one daemon thread worker per `run_func`.
- Invokes each `run_func(app)` against the same shared app.
- For uncaught worker exceptions, records traceback and closes app (fail-fast).
- Breaking behavior: uncaught `on_message` errors now fail-fast (same as `run_func`).
- Normal return of one `run_func` does not stop other workers.

Raises:

- `WorkerAlreadyRunningError` when called while worker is alive.

### 3.3 `EasierlitClient.stop`

```python
stop(timeout: float = 5.0) -> None
```

Behavior:

- Closes app.
- Stops scheduling new incoming messages and clears pending incoming dispatch buffers.
- Does not wait for in-flight message workers.
- Joins all worker threads up to `timeout` each.
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

### 4.1 `EasierlitApp.enqueue`

```python
enqueue(
    thread_id: str,
    content: str,
    session_id: str = "external",
    author: str = "User",
    message_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    elements: list[Any] | None = None,
    created_at: str | None = None,
) -> str
```

Behavior:

- Enqueues outgoing `add_message` command with `step_type="user_message"` for immediate UI/data-layer visibility.
- Builds an `IncomingMessage` and dispatches it through runtime/client on_message workers.
- Returns the enqueued `message_id`.
- Uses generated UUID message id when `message_id` is omitted.
- Raises `ValueError` when `thread_id`/`session_id`/`author` are blank.
- Raises `ValueError` when provided `message_id` is blank.
- Raises `AppClosedError` when app is already closed.

### 4.2 `EasierlitApp.add_message`

```python
add_message(
    thread_id: str,
    content: str,
    author: str = "Assistant",
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> str
```

Behavior:

- Enqueues outgoing `add_message` command.
- `elements` forwards Chainlit element objects (image/file/etc.) to runtime.
- Returns generated `message_id`.
- This call does not auto-send to Discord.
- Command is later applied by runtime dispatcher.
- Runtime dispatcher preserves outgoing order within the same `thread_id`; global cross-thread outgoing order is not guaranteed.

### 4.3 `EasierlitApp.add_tool`

```python
add_tool(
    thread_id: str,
    tool_name: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> str
```

- Enqueues outgoing `add_tool` command.
- `tool_name` is written to step `name` (`author` display in UI).
- `elements` forwards Chainlit element objects to runtime.

### 4.6 `EasierlitApp.add_thought`

```python
add_thought(
    thread_id: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> str
```

- Wrapper of `add_tool(...)` with fixed tool name `"Reasoning"`.

### 4.6.1 `EasierlitApp.send_to_discord`

```python
send_to_discord(
    thread_id: str,
    content: str,
    elements: list[Any] | None = None,
) -> bool
```

- Sends content to Discord only for the currently mapped Discord channel of `thread_id`.
- Optional `elements` are sent as Discord file attachments when resolvable.
- Returns `True` when sent, `False` when channel is not registered or send fails.
- Raises `ValueError` when `thread_id` is blank, or when both `content` and `elements` are empty.
- Does not create/update Chainlit steps or data-layer records.

### 4.6.2 `EasierlitApp.is_discord_thread`

```python
is_discord_thread(thread_id: str) -> bool
```

- Returns `True` when the thread is recognized as Discord-origin.
- Detection order:
- runtime Discord channel mapping for active sessions
- persisted thread metadata markers (`easierlit_discord_owner_id`, `client_type="discord"`, `clientType="discord"`)
- Returns `False` when no marker is found or data layer is unavailable.
- Raises `ValueError` when `thread_id` is blank.

### 4.7 `EasierlitApp.update_message`

```python
update_message(
    thread_id: str,
    message_id: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> None
```

- Enqueues outgoing `update_message` command.
- `elements` forwards Chainlit element objects to runtime.

### 4.8 `EasierlitApp.update_tool`

```python
update_tool(
    thread_id: str,
    message_id: str,
    tool_name: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> None
```

- Enqueues outgoing `update_tool` command.
- `tool_name` is written to step `name` (`author` display in UI).
- `elements` forwards Chainlit element objects to runtime.

### 4.9 `EasierlitApp.update_thought`

```python
update_thought(
    thread_id: str,
    message_id: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> None
```

- Wrapper of `update_tool(...)` with fixed tool name `"Reasoning"`.

### 4.10 `EasierlitApp.delete_message`

```python
delete_message(thread_id: str, message_id: str) -> None
```

- Enqueues outgoing `delete` command.

Message-command execution model:

1. Runtime resolves active websocket session for target thread.
2. If session exists, command applies in realtime context.
3. If session is missing and data layer exists, fallback applies via data layer with internal HTTP context.
4. If both are missing, command application raises `ThreadSessionNotActiveError`.

### 4.11 `EasierlitApp.list_threads`

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

### 4.12 `EasierlitApp.get_thread`

```python
get_thread(thread_id: str) -> dict
```

- Returns thread dict.
- Normalizes SQLite tags format.
- Raises `ValueError` if thread does not exist.

### 4.13 `EasierlitApp.get_messages`

```python
get_messages(thread_id: str) -> dict
```

Behavior:

- Loads the target thread via `get_thread(thread_id)`.
- Preserves the original order of `thread["steps"]`.
- Keeps only dictionary steps with these types: `user_message`, `assistant_message`, `system_message`, `tool`.
- Maps `thread["elements"]` to each message by `forId` aliases: `forId`, `for_id`, `stepId`, `step_id`.
- Adds `has_source` and `source` metadata to each returned element.
- `source.kind` is one of: `url`, `path`, `bytes`, `objectKey`, `chainlitKey`.
- If `url` is missing and `objectKey` exists, it attempts URL recovery from data-layer storage provider.
- Returns:
- `thread`: thread metadata without `steps`
- `messages`: one ordered list containing message/tool steps, each with `elements`

### 4.14 `EasierlitApp.new_thread`

```python
new_thread(
    name: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> str
```

Behavior:

- Generates `thread_id` internally using UUID4.
- Retries up to 16 times when generated id already exists.
- Returns the created `thread_id`.
- With auth configured, auto-resolves/creates owner user and writes `user_id`.
- SQLite SQLAlchemyDataLayer stores `tags` as JSON string.

Raises:

- `RuntimeError` if unique `thread_id` allocation fails after 16 attempts.

### 4.15 `EasierlitApp.update_thread`

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

### 4.16 `EasierlitApp.delete_thread`

```python
delete_thread(thread_id: str) -> None
```

- Deletes thread via data layer.

### 4.17 `EasierlitApp.reset_thread`

```python
reset_thread(thread_id: str) -> None
```

Behavior:

- Verifies target thread existence via `get_thread(thread_id)`.
- Collects existing step ids and applies immediate `delete` commands via runtime (realtime + data-layer fallback path).
- Deletes the thread and recreates the same `thread_id`.
- Restores only thread `name`; recreated thread `metadata` and `tags` are reset.

Raises:

- `DataPersistenceNotEnabledError` when no data layer is configured.
- `ValueError` if thread does not exist.

### 4.18 `EasierlitApp.close`

```python
close() -> None
```

Behavior:

- Marks app closed.
- Enqueues `close` outgoing command for dispatcher shutdown.

### 4.19 `EasierlitApp.is_closed`

```python
is_closed() -> bool
```

- Returns whether app is closed.

### 4.20 Discord Typing APIs

```python
discord_typing_open(thread_id: str) -> bool
discord_typing_close(thread_id: str) -> bool
```

Behavior:

- `discord_typing_open(...)` enables Discord typing indicator for a Discord-mapped thread.
- `discord_typing_close(...)` disables Discord typing indicator for a Discord-mapped thread.
- Both methods return `True` on success, `False` when no Discord mapping/sender is available.
- `thread_id` must be a non-empty string; blank values raise `ValueError`.
- Typing state is explicit and is not auto-managed around each `on_message` execution.
- Public `lock/unlock` methods are intentionally not exposed.

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
- For `EasierlitServer(auth=None)`, this config is auto-created from environment/default credentials.

### 5.2 `EasierlitPersistenceConfig`

```python
EasierlitPersistenceConfig(
    enabled: bool = True,
    sqlite_path: str = ".chainlit/easierlit.db",
    storage_provider: BaseStorageClient | Any = <auto LocalFileStorageClient>,
)
```

- `storage_provider` is forwarded to `SQLAlchemyDataLayer(storage_provider=...)`.
- Default `storage_provider` is `LocalFileStorageClient`.
- Default local storage path is `<CHAINLIT_APP_ROOT or cwd>/public/easierlit`.
- `LocalFileStorageClient(base_dir=...)` supports `~` expansion.
- Relative `base_dir` values resolve under `<CHAINLIT_APP_ROOT or cwd>/public`.
- Absolute `base_dir` values outside `public` are supported directly.
- Local files/images are served through `/easierlit/local/{object_key}`.
- Generated local file/image URLs include both `CHAINLIT_PARENT_ROOT_PATH` and `CHAINLIT_ROOT_PATH`.
- `enabled=True` requires a valid `LocalFileStorageClient`; `None` or non-local providers raise configuration errors.
- Easierlit preflights local storage upload/read/delete at startup for default persistence.

### 5.3 `EasierlitDiscordConfig`

```python
EasierlitDiscordConfig(
    enabled: bool = True,
    bot_token: str | None = None,
)
```

Behavior:

- `discord=None` on `EasierlitServer(...)` keeps Discord disabled during `serve()`.
- Passing `discord=EasierlitDiscordConfig(...)` enables Discord by default.
- `enabled=False`: Easierlit Discord bridge is not started.
- `enabled=True`: Discord bot token order is `bot_token` first (if non-empty), then `DISCORD_BOT_TOKEN` as fallback.
- Easierlit does not clear `DISCORD_BOT_TOKEN` while serving.
- Raises `ValueError` if Discord is enabled and no non-empty token is available.

### 5.4 `IncomingMessage`

```python
IncomingMessage(
    thread_id: str,
    session_id: str,
    message_id: str,
    content: str,
    elements: list[Any] = [],
    author: str,
    created_at: str | None = None,
    metadata: dict | None = None,
)
```

### 5.5 `OutgoingCommand`

```python
OutgoingCommand(
    command: Literal[
        "add_message",
        "add_tool",
        "update_message",
        "update_tool",
        "delete",
        "close",
    ],
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
| `AppClosedError` | incoming dispatch or enqueue on closed app | Stop processing and restart server if needed |
| `WorkerAlreadyRunningError` | `client.run()` called while worker alive | Call `client.stop()` first |
| `RunFuncExecutionError` | Worker raised uncaught error | Inspect traceback, fix `run_func`/`on_message` logic |
| `DataPersistenceNotEnabledError` | Thread CRUD without configured data layer | Enable persistence or register data layer |
| `ThreadSessionNotActiveError` | Applying message command without active session and without data layer | Ensure session is active or configure persistence fallback |
| `RuntimeError` | `new_thread()` failed to allocate unique ID after retries | Inspect id generation/collision behavior and retry |
| `ValueError` | Invalid worker mode/run_func mode/run_funcs, missing user/thread, invalid enqueue input | Validate inputs and identifiers |

## 7. Chainlit Message vs Tool-call Mapping

- Incoming `on_message(..., incoming)` maps to user-message flow.
- `app.enqueue(...)` mirrors input as `user_message` and dispatches to on_message.
- `app.discord_typing_open/discord_typing_close` controls Discord typing indicator.
- Outgoing `app.add_message` maps to assistant-message flow.
- Outgoing `app.add_tool/update_tool` maps to tool-call flow with step name set from `tool_name`.
- Outgoing `app.add_thought/update_thought` maps to tool-call flow with fixed step name `Reasoning`.
- `app.send_to_discord` maps to explicit Discord-only output (no step persistence).
- `app.is_discord_thread` checks Discord-origin markers.

## 8. Method-to-Example Index

| Method group | Example |
|---|---|
| `EasierlitClient.run`, `stop` | `examples/minimal.py` |
| `EasierlitApp.list_threads`, `get_thread`, `get_messages`, `new_thread`, `update_thread`, `delete_thread`, `reset_thread` | `examples/thread_crud.py`, `examples/thread_create_in_run_func.py` |
| `EasierlitApp.discord_typing_open`, `discord_typing_close` | No dedicated example yet (runtime/manual control APIs) |
| `EasierlitApp.enqueue` | In-process integrations that mirror input as `user_message` and dispatch to `on_message` |
| `EasierlitApp.add_message`, `update_message`, `delete_message` | `examples/minimal.py`, `examples/thread_create_in_run_func.py` |
| `EasierlitApp.add_tool`, `add_thought`, `update_tool`, `update_thought` | `examples/step_types.py` |
| `EasierlitApp.send_to_discord` | `examples/discord_bot.py` |
| `EasierlitApp.is_discord_thread` | No dedicated example yet (runtime/data-layer marker check) |
| Auth + persistence configs | `examples/custom_auth.py` |
| Discord config | `examples/discord_bot.py` |
