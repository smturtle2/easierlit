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
- `EasierlitClient`: runs your `run_func(app)` in one global thread worker.
- `EasierlitApp`: queue bridge for inbound/outbound communication.
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
  -> EasierlitApp incoming queue
  -> run_func(app) in worker (thread)
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
from easierlit import AppClosedError, EasierlitClient, EasierlitServer


def run_func(app):
    while True:
        try:
            incoming = app.recv(timeout=1.0)
        except TimeoutError:
            continue
        except AppClosedError:
            break

        app.add_message(
            thread_id=incoming.thread_id,
            content=f"Echo: {incoming.content}",
            author="EchoBot",
        )


client = EasierlitClient(run_func=run_func)
server = EasierlitServer(client=client)
server.serve()  # blocking
```

Async worker pattern:

```python
from easierlit import AppClosedError, EasierlitClient, EasierlitServer


async def run_func(app):
    while True:
        try:
            incoming = await app.arecv()
        except AppClosedError:
            break

        app.add_message(
            thread_id=incoming.thread_id,
            content=f"Echo: {incoming.content}",
            author="EchoBot",
        )


client = EasierlitClient(
    run_func=run_func,
    run_func_mode="auto",  # auto/sync/async
)
server = EasierlitServer(client=client)
server.serve()
```

## Public API

```python
EasierlitServer(
    client,
    host="127.0.0.1",
    port=8000,
    root_path="",
    auth=None,
    persistence=None,
    discord=None,
)

EasierlitClient(run_func, worker_mode="thread", run_func_mode="auto")

EasierlitApp.recv(timeout=None)
EasierlitApp.arecv(timeout=None)
EasierlitApp.add_message(thread_id, content, author="Assistant", metadata=None) -> str
EasierlitApp.add_tool(thread_id, tool_name, content, metadata=None) -> str
EasierlitApp.add_thought(thread_id, content, metadata=None) -> str  # tool_name is fixed to "Reasoning"
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
- Default local storage path: `public/easierlit`
- If SQLite schema is incompatible, Easierlit recreates DB with backup
- Sidebar default state is forced to `open`
- Discord integration is disabled by default during `serve()`, even if `DISCORD_BOT_TOKEN` already exists.

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
- Easierlit runs Discord through its own bridge (no runtime monkeypatching of Chainlit Discord handlers).
- During `serve()`, Chainlit's `DISCORD_BOT_TOKEN` path stays disabled; Easierlit restores the original env value after shutdown.
- If enabled and no non-empty token is available, `serve()` raises `ValueError`.

## Message and Thread Operations

Message APIs:

- `app.add_message(...)`
- `app.add_tool(...)`
- `app.add_thought(...)`
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

Behavior highlights:

- `app.add_message(...)` returns generated `message_id`.
- `app.add_tool(...)` stores tool-call steps with tool name shown as step author/name.
- `app.add_thought(...)` is the same tool-call path with fixed tool name `Reasoning`.
- `app.get_messages(...)` returns thread metadata plus one ordered `messages` list.
- `app.get_messages(...)` includes `user_message`/`assistant_message`/`system_message`/`tool` and excludes run-family steps.
- `app.get_messages(...)` maps `thread["elements"]` into each message via `forId`, so image/file elements are available per message.
- `app.new_thread(...)` auto-generates a unique `thread_id` and returns it.
- `app.update_thread(...)` updates only when thread already exists.
- With auth enabled, both `app.new_thread(...)` and `app.update_thread(...)` auto-assign thread ownership.
- SQLite SQLAlchemyDataLayer path auto normalizes thread `tags`.
- If no active websocket session exists, Easierlit applies internal HTTP-context fallback for data-layer message CRUD.

## Worker Failure Policy

Easierlit uses fail-fast behavior for worker crashes.

- If `run_func` raises, server shutdown is triggered.
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
