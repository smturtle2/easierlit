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
- `EasierlitClient`: starts your `run_func(app)` in one global thread worker.
- `EasierlitApp`: queue bridge for inbound user messages and outbound commands.

High-level flow:

1. `server.serve()` binds runtime and starts Chainlit.
2. Chainlit callback `on_message` converts input into `IncomingMessage`.
3. Worker calls `app.recv()` or `await app.arecv()` and handles message.
4. Worker returns output via `app.*` APIs (message + thread CRUD).

## 3. Canonical Bootstrapping Pattern

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
server.serve()
```

Notes:

- `serve()` is blocking.
- `worker_mode` supports only `"thread"`.
- `run_func` can be sync or async. `run_func_mode="auto"` detects and runs both.

## 4. Public API Signatures

This section is a quick signature summary. Full method contracts are in `docs/api-reference.en.md`.

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

## 5. Server Runtime Policies

Easierlit server enforces these defaults:

- Chainlit headless mode enabled.
- Sidebar default state forced to `open`.
- CoT mode forced to `full`.
- `CHAINLIT_AUTH_COOKIE_NAME` is preserved if already set; otherwise Easierlit sets `easierlit_access_token_<hash>`.
- If `CHAINLIT_AUTH_SECRET` is set but shorter than 32 bytes, Easierlit replaces it with a secure generated secret for the current run; if missing, Easierlit auto-manages `.chainlit/jwt.secret`.
- Easierlit restores previous `CHAINLIT_AUTH_COOKIE_NAME` and `CHAINLIT_AUTH_SECRET` after shutdown.
- `UVICORN_WS_PROTOCOL` defaults to `websockets-sansio` when not set.
- `run_func` fail-fast: worker exception triggers server shutdown.
- Discord integration is disabled by default during `serve()`, even if `DISCORD_BOT_TOKEN` is already set.

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
- Absolute `base_dir` values outside `public` are exposed via a symlink under `public/.easierlit-external/`.
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
- Easierlit runs Discord via its own bridge (no runtime monkeypatching of Chainlit Discord handlers).
- During `serve()`, Chainlit's `DISCORD_BOT_TOKEN` startup path is kept disabled and the original env value is restored after shutdown.

Thread History visibility follows Chainlit policy:

- `requireLogin=True`
- `dataPersistence=True`

## 7. run_func Pattern and Error Handling

Recommended structure:

1. Sync `run_func`: long-running loop with `app.recv(timeout=...)`.
2. Async `run_func`: long-running loop with `await app.arecv()` (or `await app.arecv(timeout=...)` when needed).
3. Handle `TimeoutError` as idle tick when using timeout.
4. Break on `AppClosedError`.
5. Keep per-command exceptions contextual for logs.

If `run_func` raises uncaught exception:

- Easierlit logs traceback.
- Easierlit triggers server shutdown.
- Further incoming enqueue attempts are suppressed with shutdown messaging.

## 8. Thread CRUD in App

Available methods on `EasierlitApp`:

- `list_threads(first=20, cursor=None, search=None, user_identifier=None)`
- `get_thread(thread_id)`
- `get_messages(thread_id) -> dict`
- `new_thread(name=None, metadata=None, tags=None) -> str`
- `update_thread(thread_id, name=None, metadata=None, tags=None)`
- `delete_thread(thread_id)`

Behavior details:

- Data layer is required for thread CRUD.
- `new_thread` auto-generates a unique thread id and returns it.
- `update_thread` updates only when target thread already exists.
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

Execution model:

1. If thread has active websocket session, message applies in realtime context.
2. If session inactive and data layer exists, Easierlit runs persistence fallback.
3. Fallback initializes internal HTTP Chainlit context before step CRUD.
4. If no session and no data layer, `ThreadSessionNotActiveError` is raised when queued command is applied.

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

- Incoming `app.recv()` data is user-message flow.
- Incoming `app.arecv()` data follows the same user-message flow contract.
- Outgoing `app.add_message()` is assistant-message flow.
- Outgoing `app.add_tool()/app.update_tool()` is tool-call flow with step name=`tool_name`.
- Outgoing `app.add_thought()/app.update_thought()` is tool-call flow with fixed step name=`Reasoning`.

UI option reference (Chainlit): `ui.cot` supports `full`, `tool_call`, `hidden`.

## 12. Troubleshooting

`Cannot enqueue incoming message to a closed app`:

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
