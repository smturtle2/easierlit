# Easierlit Usage Guide (v0.3.1)

This document is the detailed usage reference for Easierlit v0.3.1.
For exact method-level contracts (signature, raises, failure modes), see:

- `docs/api-reference.en.md`
- `docs/api-reference.ko.md`

## 1. Scope and Version

- Version target: `0.3.1`
- Runtime core: Chainlit (`chainlit>=2.9,<3`)
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

        app.send(
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
)

EasierlitClient(run_func, worker_mode="thread", run_func_mode="auto")

EasierlitApp.recv(timeout=None)
EasierlitApp.arecv(timeout=None)
EasierlitApp.send(thread_id, content, author="Assistant", metadata=None) -> str
EasierlitApp.add_message(thread_id, content, author="Assistant", metadata=None) -> str  # deprecated alias
EasierlitApp.update_message(thread_id, message_id, content, metadata=None)
EasierlitApp.delete_message(thread_id, message_id)
EasierlitApp.list_threads(first=20, cursor=None, search=None, user_identifier=None)
EasierlitApp.get_thread(thread_id)
EasierlitApp.new_thread(name=None, metadata=None, tags=None) -> str
EasierlitApp.update_thread(thread_id, name=None, metadata=None, tags=None)
EasierlitApp.delete_thread(thread_id)
EasierlitApp.close()

EasierlitAuthConfig(username, password, identifier=None, metadata=None)
EasierlitPersistenceConfig(enabled=True, sqlite_path=".chainlit/easierlit.db")
```

## 5. Server Runtime Policies

Easierlit server enforces these defaults:

- Chainlit headless mode enabled.
- Sidebar default state forced to `open`.
- `CHAINLIT_AUTH_COOKIE_NAME=easierlit_access_token`.
- JWT secret auto-managed in `.chainlit/jwt.secret`.
- `run_func` fail-fast: worker exception triggers server shutdown.

## 6. Auth and Persistence

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
from easierlit import EasierlitPersistenceConfig, EasierlitServer

persistence = EasierlitPersistenceConfig(
    enabled=True,
    sqlite_path=".chainlit/easierlit.db",
)

server = EasierlitServer(client=client, persistence=persistence)
```

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
- `new_thread(name=None, metadata=None, tags=None) -> str`
- `update_thread(thread_id, name=None, metadata=None, tags=None)`
- `delete_thread(thread_id)`

Behavior details:

- Data layer is required for thread CRUD.
- `new_thread` auto-generates a unique thread id and returns it.
- `update_thread` updates only when target thread already exists.
- If auth is configured, `new_thread` and `update_thread` auto-resolve owner user and save with `user_id`.
- In SQLite SQLAlchemyDataLayer, `tags` list is JSON-serialized on write and normalized to list on read.

## 9. Message CRUD and Fallback

Message methods:

- `app.send(...)`, `app.add_message(...)` (deprecated alias), `app.update_message(...)`, `app.delete_message(...)`

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
3. Call `app.send(...)` to add bootstrap assistant message.
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

Easierlit v0.3.1 mapping:

- Incoming `app.recv()` data is user-message flow.
- Incoming `app.arecv()` data follows the same user-message flow contract.
- Outgoing `app.send()` is assistant-message flow (`app.add_message()` is a deprecated alias).
- Easierlit does not provide a dedicated public API to create tool-call steps.

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
- Action: restart server and login again (cookie name is `easierlit_access_token`).

SQLite `tags` binding issues:

- Easierlit normalizes `tags` for SQLite SQLAlchemyDataLayer.
- If issue persists, ensure runtime imports this project build and not stale install.

## 13. Examples

- `examples/minimal.py`
- `examples/custom_auth.py`
- `examples/thread_crud.py`
- `examples/thread_create_in_run_func.py`

## 14. Release Checklist (v0.3.1)

```bash
python3 -m py_compile examples/*.py
python3 -m pytest
python3 -m build
python3 -m twine check dist/*
```

Also verify:

- `pyproject.toml` version is `0.3.1`
- README/doc links resolve (`README.md`, `README.ko.md`, `docs/usage.en.md`, `docs/usage.ko.md`, `docs/api-reference.en.md`, `docs/api-reference.ko.md`)
