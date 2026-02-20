[English](README.md) | [한국어](README.ko.md)

# Easierlit

[![Python](https://img.shields.io/badge/python-3.13%2B-0ea5e9)](pyproject.toml)
[![Chainlit](https://img.shields.io/badge/chainlit-2.9%20to%203-10b981)](https://docs.chainlit.io)

Easierlit is a Python-first wrapper around Chainlit.
It keeps the power of Chainlit while reducing the boilerplate for worker loops, message flow, auth, and persistence.

## Quick Links

- Installation: [Install](#install)
- Start in 60 seconds: [Quick Start](#quick-start-60-seconds)
- Method contracts: [`docs/api-reference.en.md`](docs/api-reference.en.md)
- Full usage guide: [`docs/usage.en.md`](docs/usage.en.md)
- Korean docs: [`README.ko.md`](README.ko.md), [`docs/api-reference.ko.md`](docs/api-reference.ko.md), [`docs/usage.ko.md`](docs/usage.ko.md)

## Why Easierlit

- Clear runtime split:
- `EasierlitServer`: runs Chainlit in the main process.
- `EasierlitClient`: dispatches incoming messages to `on_message(app, incoming)` workers.
- `EasierlitApp`: message/thread CRUD bridge for outgoing commands.
- Production-oriented defaults:
- headless server mode
- sidebar default state `open`
- JWT secret auto-management (`.chainlit/jwt.secret`)
- scoped auth cookie default (`easierlit_access_token_<hash>`)
- fail-fast worker policy
- Practical persistence behavior:
- default SQLite bootstrap (`.chainlit/easierlit.db`)
- schema compatibility recovery
- SQLite `tags` normalization for thread CRUD

## Architecture at a Glance

```text
User UI
  -> Chainlit callbacks (on_message / on_chat_start / ...)
  -> Easierlit runtime bridge
  -> EasierlitClient incoming dispatcher
  -> on_message(app, incoming) in per-message workers (thread)
  -> app.* APIs (message + thread CRUD)
  -> runtime dispatcher
  -> realtime session OR data-layer fallback
```

## Install

```bash
pip install easierlit
```

For local development:

```bash
pip install -e ".[dev]"
```

## Quick Start (60 Seconds)

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
server.serve()  # blocking
```

Optional background run_func pattern:

```python
import time

from easierlit import EasierlitClient, EasierlitServer


def on_message(app, incoming):
    app.add_message(incoming.thread_id, f"Echo: {incoming.content}", author="EchoBot")


def run_func(app):
    while not app.is_closed():
        # Optional background worker; no inbound message polling.
        time.sleep(0.2)


client = EasierlitClient(
    on_message=on_message,
    run_funcs=[run_func],  # optional
    run_func_mode="auto",  # auto/sync/async
)
server = EasierlitServer(client=client)
server.serve()
```

Image element example (without Markdown):

```python
from chainlit.element import Image


image = Image(name="diagram.png", path="/absolute/path/diagram.png")
app.add_message(
    thread_id=incoming.thread_id,
    content="Attached image",
    elements=[image],
)
```

External in-process enqueue example:

```python
message_id = app.enqueue(
    thread_id="thread-external",
    content="hello from external integration",
    session_id="webhook-1",
    author="Webhook",
)
```

## Public API

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
EasierlitApp.add_message(thread_id, content, author="Assistant", metadata=None, elements=None) -> str
EasierlitApp.add_tool(thread_id, tool_name, content, metadata=None, elements=None) -> str
EasierlitApp.add_thought(thread_id, content, metadata=None, elements=None) -> str  # tool_name is fixed to "Reasoning"
EasierlitApp.send_to_discord(thread_id, content) -> bool
EasierlitApp.update_message(thread_id, message_id, content, metadata=None, elements=None)
EasierlitApp.update_tool(thread_id, message_id, tool_name, content, metadata=None, elements=None)
EasierlitApp.update_thought(thread_id, message_id, content, metadata=None, elements=None)  # tool_name is fixed to "Reasoning"
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

For exact method contracts, use:

- `docs/api-reference.en.md`

This includes parameter constraints, return semantics, exceptions, side effects, concurrency notes, and failure-mode fixes for each public method.

## Auth and Persistence Defaults

- JWT secret: if `CHAINLIT_AUTH_SECRET` is set but shorter than 32 bytes, Easierlit replaces it with a secure generated secret for the current run; if missing, it auto-manages `.chainlit/jwt.secret`
- Auth cookie: keeps `CHAINLIT_AUTH_COOKIE_NAME` when set, otherwise uses scoped default `easierlit_access_token_<hash>`
- On shutdown, Easierlit restores the previous `CHAINLIT_AUTH_COOKIE_NAME` and `CHAINLIT_AUTH_SECRET`
- `UVICORN_WS_PROTOCOL` defaults to `websockets-sansio` when not set
- Default auth is enabled when `auth=None`
- Auth credential order for `auth=None`:
- `EASIERLIT_AUTH_USERNAME` + `EASIERLIT_AUTH_PASSWORD` (must be set together)
- fallback to `admin` / `admin` (warning log emitted)
- Default persistence: SQLite at `.chainlit/easierlit.db` (threads + text steps)
- Default file/image storage: `LocalFileStorageClient` is always enabled by default
- Default local storage path: `<CHAINLIT_APP_ROOT or cwd>/public/easierlit`
- `LocalFileStorageClient(base_dir=...)` supports `~` expansion
- Relative `base_dir` values resolve under `<CHAINLIT_APP_ROOT or cwd>/public`
- Absolute `base_dir` values outside `public` are supported directly
- Local files/images are served through `/easierlit/local/{object_key}`
- Local file/image URLs include both `CHAINLIT_PARENT_ROOT_PATH` and `CHAINLIT_ROOT_PATH` prefixes
- If SQLite schema is incompatible, Easierlit recreates DB with backup
- Sidebar default state is forced to `open`
- Discord bridge is disabled by default unless `discord=EasierlitDiscordConfig(...)` is provided.

Thread History sidebar visibility follows Chainlit policy:

- `requireLogin=True`
- `dataPersistence=True`

Typical Easierlit setup:

- keep `auth=None` and `persistence=None` for default enabled auth + persistence
- optionally set `EASIERLIT_AUTH_USERNAME`/`EASIERLIT_AUTH_PASSWORD` for non-default credentials
- pass `persistence=EasierlitPersistenceConfig(storage_provider=LocalFileStorageClient(...))` to override local storage path/behavior
- or pass explicit `auth=EasierlitAuthConfig(...)`

Discord bot setup:

- Keep `discord=None` to disable Discord integration.
- Pass `discord=EasierlitDiscordConfig(...)` to enable it.
- Token precedence: `EasierlitDiscordConfig.bot_token` first, `DISCORD_BOT_TOKEN` fallback.
- Discord replies are explicit: call `app.send_to_discord(...)` when needed.
- Discord-origin threads are upserted with runtime auth owner for stable Thread History visibility.
- Easierlit runs Discord through its own bridge (no runtime monkeypatching of Chainlit Discord handlers).
- During `serve()`, Easierlit does not clear `DISCORD_BOT_TOKEN`; the env value remains unchanged.
- If enabled and no non-empty token is available, `serve()` raises `ValueError`.

## Message and Thread Operations

Message APIs:

- `app.add_message(...)`
- `app.add_tool(...)`
- `app.add_thought(...)`
- `app.send_to_discord(...)`
- `app.update_message(...)`
- `app.update_tool(...)`
- `app.update_thought(...)`
- `app.delete_message(...)`

Thread APIs:

- `app.list_threads(...)`
- `app.get_thread(thread_id)`
- `app.get_messages(thread_id)`
- `app.new_thread(...)`
- `app.update_thread(...)`
- `app.delete_thread(thread_id)`
- `app.reset_thread(thread_id)`

Thread task-state APIs:

- `app.start_thread_task(thread_id)`
- `app.end_thread_task(thread_id)`
- `app.is_thread_task_running(thread_id)`

Behavior highlights:

- `app.add_message(...)` returns generated `message_id`.
- `app.enqueue(...)` mirrors input as `user_message` (UI/data layer) and dispatches to `on_message`.
- `app.add_tool(...)` stores tool-call steps with tool name shown as step author/name.
- `app.add_thought(...)` is the same tool-call path with fixed tool name `Reasoning`.
- `app.add_message(...)`/`app.add_tool(...)`/`app.add_thought(...)` no longer auto-send to Discord.
- `app.send_to_discord(...)` sends only to Discord and returns `True/False`.
- `app.start_thread_task(...)` marks one thread as working (UI task indicator).
- `app.end_thread_task(...)` clears working state (UI task indicator).
- `app.is_thread_task_running(...)` returns current thread working state.
- Easierlit auto-manages thread task state around each `on_message` execution.
- Async awaitable execution is isolated by role:
- `run_func` awaitables run on a dedicated runner loop.
- `on_message` awaitables run on a thread-aware runner pool sized as `min(max_message_workers, 8)`.
- Same `thread_id` is pinned to the same `on_message` runner lane.
- Runtime outgoing dispatcher uses thread-aware parallel lanes: same `thread_id` order is preserved, but global cross-thread outgoing order is not guaranteed.
- CPU-bound Python handlers still share the GIL; use process-level offloading when true CPU isolation is required.
- `app.get_messages(...)` returns thread metadata plus one ordered `messages` list.
- `app.get_messages(...)` includes `user_message`/`assistant_message`/`system_message`/`tool` and excludes run-family steps.
- `app.get_messages(...)` maps `thread["elements"]` into each message via `forId` aliases (`forId`/`for_id`/`stepId`/`step_id`).
- `app.get_messages(...)` adds `elements[*].has_source` and `elements[*].source` (`url`/`path`/`bytes`/`objectKey`/`chainlitKey`) for image/file source tracing.
- `app.new_thread(...)` auto-generates a unique `thread_id` and returns it.
- `app.update_thread(...)` updates only when thread already exists.
- `app.delete_thread(...)` and `app.reset_thread(...)` automatically clear thread task state.
- With auth enabled, both `app.new_thread(...)` and `app.update_thread(...)` auto-assign thread ownership.
- SQLite SQLAlchemyDataLayer path auto normalizes thread `tags`.
- If no active websocket session exists, Easierlit applies internal HTTP-context fallback for data-layer message CRUD.
- Public `lock/unlock` APIs are intentionally not exposed.

## Worker Failure Policy

Easierlit uses fail-fast behavior for worker crashes.

- If any `run_func` or `on_message` raises, server shutdown is triggered.
- UI gets a short summary when possible.
- Full traceback is kept in server logs.

## Chainlit Message vs Tool-call

Chainlit distinguishes message and tool/run categories at step type level.

Message steps:

- `user_message`
- `assistant_message`
- `system_message`

Tool/run family includes:

- `tool`, `run`, `llm`, `embedding`, `retrieval`, `rerank`, `undefined`

Easierlit mapping:

- `app.add_message(...)` -> `assistant_message`
- `app.add_tool(...)` / `app.update_tool(...)` -> `tool`
- `app.add_thought(...)` / `app.update_thought(...)` -> `tool` (name fixed to `Reasoning`)
- `app.send_to_discord(...)` sends an explicit Discord reply without creating a step.
- `app.delete_message(...)` deletes by `message_id` regardless of message/tool/thought source.

## Example Map

- `examples/minimal.py`: basic echo bot
- `examples/custom_auth.py`: single-account auth
- `examples/discord_bot.py`: Discord bot configuration and token precedence
- `examples/thread_crud.py`: thread list/get/update/delete
- `examples/thread_create_in_run_func.py`: create thread from `run_func`
- `examples/step_types.py`: tool/thought step creation, update, delete example

## Documentation Map

- Method-level API contracts (EN): `docs/api-reference.en.md`
- Method-level API contracts (KO): `docs/api-reference.ko.md`
- Full usage guide (EN): `docs/usage.en.md`
- Full usage guide (KO): `docs/usage.ko.md`

## Migration Note

API updates:

- `new_thread(thread_id=..., ...)` -> `thread_id = new_thread(...)`
- `send(...)` was removed.
- `add_message(...)` is now the canonical message API.
- Added tool/thought APIs: `add_tool(...)`, `add_thought(...)`, `update_tool(...)`, `update_thought(...)`.
- Breaking behavior: `on_message` exceptions are now fail-fast (same as `run_func`) and no longer emit an internal notice then continue.
