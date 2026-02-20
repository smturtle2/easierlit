# Easierlit Usage Guide

This document is the detailed usage reference for Easierlit.
For exact method-level contracts (signature, raises, failure modes), see:

- `docs/api-reference.en.md`
- `docs/api-reference.ko.md`

## 1. Scope

- Runtime core: Chainlit (`chainlit>=2.9.6,<3`)
- This guide covers current public APIs only.

## 2. Architecture

Easierlit has three core parts:

- `EasierlitServer`: starts Chainlit in the main process.
- `EasierlitClient`: dispatches incoming messages to `on_message(app, incoming)` workers.
- `EasierlitApp`: message/thread CRUD bridge for outbound commands.

High-level flow:

1. `server.serve()` binds runtime and starts Chainlit.
2. Chainlit callback `on_message` converts input into `IncomingMessage`.
3. Runtime dispatches input to `client.on_message(app, incoming)` in message workers.
4. Handler returns output via `app.*` APIs (message + thread CRUD).

## 3. Canonical Bootstrapping Pattern

```python
from easierlit import EasierlitClient, EasierlitServer


def on_message(app, incoming):
    app.add_message(
        thread_id=incoming.thread_id,
        content=f"Echo: {incoming.content}",
        author="EchoBot",
    )


client = EasierlitClient(on_message=on_message)
server = EasierlitServer(client=client)
server.serve()
```

Notes:

- `serve()` is blocking.
- `worker_mode` supports only `"thread"`.
- `on_message` can be sync or async.
- `run_funcs` is optional for background tasks.
- `run_func_mode="auto"` detects sync/async behavior for each function.
- Async awaitables run on a dedicated event-loop runner (avoids per-message `asyncio.run(...)` loop bootstrap).
- CPU-bound Python code still shares the GIL; use process-level offloading for true CPU isolation.

## 4. Public API Signatures

This section is a quick signature summary. Full method contracts are in `docs/api-reference.en.md`.

```python
EasierlitServer(
    client,
    host="127.0.0.1",
    port=8000,
    root_path="",
    max_outgoing_workers=4,
    auth=None,
    persistence=None,
    discord=None,
)

EasierlitClient(
    on_message,
    run_funcs=None,
    worker_mode="thread",
    run_func_mode="auto",
    max_message_workers=64,
)

EasierlitApp.start_thread_task(thread_id)
EasierlitApp.end_thread_task(thread_id)
EasierlitApp.is_thread_task_running(thread_id) -> bool
EasierlitApp.enqueue(thread_id, content, session_id="external", author="User", message_id=None, metadata=None, elements=None, created_at=None) -> str
EasierlitApp.add_message(thread_id, content, author="Assistant", metadata=None) -> str
EasierlitApp.add_tool(thread_id, tool_name, content, metadata=None) -> str
EasierlitApp.add_thought(thread_id, content, metadata=None) -> str  # tool_name is fixed to "Reasoning"
EasierlitApp.send_to_discord(thread_id, content) -> bool
EasierlitApp.update_message(thread_id, message_id, content, metadata=None)
EasierlitApp.update_tool(thread_id, message_id, tool_name, content, metadata=None)
EasierlitApp.update_thought(thread_id, message_id, content, metadata=None)  # tool_name is fixed to "Reasoning"
EasierlitApp.delete_message(thread_id, message_id)
EasierlitApp.list_threads(first=20, cursor=None, search=None, user_identifier=None)
EasierlitApp.get_thread(thread_id)
EasierlitApp.get_messages(thread_id) -> dict
EasierlitApp.new_thread(name=None, metadata=None, tags=None) -> str
EasierlitApp.update_thread(thread_id, name=None, metadata=None, tags=None)
EasierlitApp.delete_thread(thread_id)
EasierlitApp.reset_thread(thread_id)
EasierlitApp.close()

EasierlitAuthConfig(username, password, identifier=None, metadata=None)
EasierlitPersistenceConfig(
    enabled=True,
    sqlite_path=".chainlit/easierlit.db",
    storage_provider=<auto LocalFileStorageClient>,
)
EasierlitDiscordConfig(enabled=True, bot_token=None)
```

## 5. Server Runtime Policies

Easierlit server enforces these defaults:

- Chainlit headless mode enabled.
- Sidebar default state forced to `open`.
- CoT mode forced to `full`.
- `CHAINLIT_AUTH_COOKIE_NAME` is preserved if already set; otherwise Easierlit sets `easierlit_access_token_<hash>`.
- If `CHAINLIT_AUTH_SECRET` is set but shorter than 32 bytes, Easierlit replaces it with a secure generated secret for the current run; if missing, Easierlit auto-manages `.chainlit/jwt.secret`.
- Easierlit restores previous `CHAINLIT_AUTH_COOKIE_NAME` and `CHAINLIT_AUTH_SECRET` after shutdown.
- `UVICORN_WS_PROTOCOL` defaults to `websockets-sansio` when not set.
- Worker fail-fast: any `run_func` or `on_message` exception triggers server shutdown.
- Outgoing dispatcher runs with thread-aware parallel lanes (`max_outgoing_workers`, default `4`).
- Outgoing order is guaranteed within the same `thread_id`, but cross-thread global order is not guaranteed.
- Discord bridge is disabled by default unless `discord=EasierlitDiscordConfig(...)` is provided.
- Async awaitables are isolated by role:
- `run_func` awaitables use a dedicated runner loop.
- `on_message` awaitables use a thread-aware runner pool sized as `min(max_message_workers, 8)`.
- Same `thread_id` stays pinned to one message runner lane.

## 6. Auth, Persistence, and Discord

Default behavior when omitted:

- `auth=None`: auth is enabled automatically.
- Auth credentials are resolved in order:
- `EASIERLIT_AUTH_USERNAME` + `EASIERLIT_AUTH_PASSWORD` (must both be set).
- fallback `admin` / `admin` (warning log emitted).
- setting only one of `EASIERLIT_AUTH_USERNAME` or `EASIERLIT_AUTH_PASSWORD` raises `ValueError`.
- `persistence=None`: default SQLite persistence is enabled at `.chainlit/easierlit.db`.
- Default file/image storage always uses `LocalFileStorageClient`.
- Default local storage path is `<CHAINLIT_APP_ROOT or cwd>/public/easierlit`.
- `LocalFileStorageClient(base_dir=...)` supports `~` expansion.
- Relative `base_dir` values resolve under `<CHAINLIT_APP_ROOT or cwd>/public`.
- Absolute `base_dir` values outside `public` are supported directly.
- Local files/images are served through `/easierlit/local/{object_key}`.
- Local file/image URLs automatically include `CHAINLIT_PARENT_ROOT_PATH` + `CHAINLIT_ROOT_PATH`.

Auth setup example:

```python
from easierlit import EasierlitAuthConfig, EasierlitServer

auth = EasierlitAuthConfig(
    username="admin",
    password="admin",
    identifier="admin",
    metadata={"role": "admin"},
)

server = EasierlitServer(client=client, auth=auth)
```

Persistence setup example:

```python
from easierlit import EasierlitPersistenceConfig, EasierlitServer, LocalFileStorageClient

persistence = EasierlitPersistenceConfig(
    enabled=True,
    sqlite_path=".chainlit/easierlit.db",
    storage_provider=LocalFileStorageClient(...),  # Optional override. Must be a LocalFileStorageClient.
)

server = EasierlitServer(client=client, persistence=persistence)
```

Discord setup example:

```python
import os

from easierlit import EasierlitDiscordConfig, EasierlitServer

# Config token takes precedence over environment token.
discord = EasierlitDiscordConfig(
    bot_token=os.environ.get("MY_DISCORD_TOKEN"),
)

server = EasierlitServer(client=client, discord=discord)
```

Discord token resolution policy:

- `discord=None` keeps Discord disabled.
- Passing `discord=EasierlitDiscordConfig(...)` enables Discord by default.
- `EasierlitDiscordConfig.bot_token` is used first when non-empty.
- If it is missing, `DISCORD_BOT_TOKEN` is used as fallback.
- If Discord is enabled and no non-empty token is available, `serve()` raises `ValueError`.
- Discord replies are explicit via `app.send_to_discord(...)`.
- Discord-origin threads are upserted with runtime auth owner to keep Thread History visibility stable.
- Easierlit runs Discord via its own bridge (no runtime monkeypatching of Chainlit Discord handlers).
- During `serve()`, Easierlit does not clear `DISCORD_BOT_TOKEN`; the env value remains unchanged.

Thread History visibility follows Chainlit policy:

- `requireLogin=True`
- `dataPersistence=True`

## 7. on_message Pattern and Error Handling

Recommended structure:

1. Implement `on_message(app, incoming)` as your primary input handler.
2. Keep handler idempotent and thread-safe for concurrent chats.
3. Use `app.*` APIs for all outputs and CRUD operations.
4. Keep per-command exceptions contextual for logs.

If `on_message` or `run_func` raises uncaught exception:

- Easierlit logs traceback.
- Easierlit triggers server shutdown.
- Further incoming dispatch attempts are suppressed with shutdown messaging.
- Breaking behavior: `on_message` no longer emits an internal notice and continue.

External in-process input:

- `app.enqueue(...)` mirrors input as `user_message` for UI/data-layer visibility and dispatches to `on_message`.
- Typical usage is webhook/internal integration code that shares the same process.

Thread task-state API:

- `start_thread_task(thread_id)`
- `end_thread_task(thread_id)`
- `is_thread_task_running(thread_id) -> bool`

Behavior:

- `start_thread_task(...)` marks a thread as working (UI task indicator).
- `end_thread_task(...)` clears working state (UI task indicator).
- Easierlit auto-manages task state for each `on_message` execution.
- Public `lock/unlock` methods are intentionally not exposed.

## 8. Thread CRUD in App

Available methods on `EasierlitApp`:

- `list_threads(first=20, cursor=None, search=None, user_identifier=None)`
- `get_thread(thread_id)`
- `get_messages(thread_id) -> dict`
- `new_thread(name=None, metadata=None, tags=None) -> str`
- `update_thread(thread_id, name=None, metadata=None, tags=None)`
- `delete_thread(thread_id)`
- `reset_thread(thread_id)`

Behavior details:

- Data layer is required for thread CRUD.
- `new_thread` auto-generates a unique thread id and returns it.
- `update_thread` updates only when target thread already exists.
- `reset_thread` deletes all thread messages and recreates the same thread id while restoring only thread `name`.
- `delete_thread` and `reset_thread` automatically clear thread task state for that thread.
- `get_messages` returns thread metadata and one ordered `messages` list.
- `get_messages` keeps only `user_message`/`assistant_message`/`system_message`/`tool` step types.
- `get_messages` maps `thread["elements"]` by `forId` aliases: `forId`, `for_id`, `stepId`, `step_id`.
- `get_messages` enriches each returned element with `has_source` and `source` (`url`/`path`/`bytes`/`objectKey`/`chainlitKey`).
- If auth is configured, `new_thread` and `update_thread` auto-resolve owner user and save with `user_id`.
- In SQLite SQLAlchemyDataLayer, `tags` list is JSON-serialized on write and normalized to list on read.

## 9. Message CRUD and Fallback

Message methods:

- `app.add_message(...)`, `app.update_message(...)`, `app.delete_message(...)`
- `app.add_tool(...)`, `app.add_thought(...)`, `app.update_tool(...)`, `app.update_thought(...)`
- `app.send_to_discord(...)` for explicit Discord-only replies

Execution model:

1. If thread has active websocket session, message applies in realtime context.
2. If session inactive and data layer exists, Easierlit runs persistence fallback.
3. Fallback initializes internal HTTP Chainlit context before step CRUD.
4. If no session and no data layer, `ThreadSessionNotActiveError` is raised when queued command is applied.
5. Outgoing commands are processed by `thread_id` lane. Same-thread order is preserved; different threads may complete out of global order.
6. `app.add_message(...)`/`app.add_tool(...)`/`app.add_thought(...)` do not auto-send to Discord.
7. Use `app.send_to_discord(...)` when a Discord reply is required.

## 10. Creating Threads from run_func

Reference example: `examples/thread_create_in_run_func.py`

Pattern:

1. Call `thread_id = app.new_thread(...)`.
2. Use the returned `thread_id` for follow-up messages.
3. Call `app.add_message(...)` to add bootstrap assistant message.
4. Reply to the current thread with created ID.

With auth enabled, created thread ownership is auto-assigned to the configured user.

## 11. Message vs Tool Call (Chainlit)

Chainlit distinguishes by step type.

Message types:

- `user_message`
- `assistant_message`
- `system_message`

Tool/run family includes:

- `tool`, `run`, `llm`, `embedding`, `retrieval`, `rerank`, `undefined`

Easierlit mapping:

- Incoming `on_message(..., incoming)` data is user-message flow.
- Outgoing `app.add_message()` is assistant-message flow.
- Outgoing `app.add_tool()/app.update_tool()` is tool-call flow with step name=`tool_name`.
- Outgoing `app.add_thought()/app.update_thought()` is tool-call flow with fixed step name=`Reasoning`.
- `app.send_to_discord()` sends explicit Discord output without creating a step.

UI option reference (Chainlit): `ui.cot` supports `full`, `tool_call`, `hidden`.

## 12. Troubleshooting

`Cannot dispatch incoming message to a closed app`:

- Meaning: worker/app already closed, usually after `run_func` crash.
- Action: inspect server traceback, fix root error, restart server.

`Data persistence is not enabled`:

- Meaning: thread CRUD or fallback requires data layer.
- Action: enable persistence (default) or configure external data layer.

`Invalid authentication token` after config changes:

- Meaning: stale browser token or secret mismatch.
- Action: restart server and login again (`CHAINLIT_AUTH_COOKIE_NAME` may be custom or `easierlit_access_token_<hash>`).

SQLite `tags` binding issues:

- Easierlit normalizes `tags` for SQLite SQLAlchemyDataLayer.
- If issue persists, ensure runtime imports this project build and not stale install.

## 13. Examples

- `examples/minimal.py`
- `examples/custom_auth.py`
- `examples/discord_bot.py`
- `examples/thread_crud.py`
- `examples/thread_create_in_run_func.py`
- `examples/step_types.py`

## 14. Release Checklist

```bash
python3 -m py_compile examples/*.py
python3 -m pytest
python3 -m build
python3 -m twine check dist/*
```

Also verify:

- `pyproject.toml` version matches the release tag
- README/doc links resolve (`README.md`, `README.ko.md`, `docs/usage.en.md`, `docs/usage.ko.md`, `docs/api-reference.en.md`, `docs/api-reference.ko.md`)
