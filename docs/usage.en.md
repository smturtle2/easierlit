# Easierlit Usage Guide (v0.1.0)

This document is the detailed usage reference for Easierlit v0.1.0.
For exact method-level contracts (signature, raises, failure modes), see:

- `docs/api-reference.en.md`
- `docs/api-reference.ko.md`

## 1. Scope and Version

- Version target: `0.1.0`
- Runtime core: Chainlit (`chainlit>=2.9,<3`)
- This guide covers current public APIs only.

## 2. Architecture

Easierlit has three core parts:

- `EasierlitServer`: starts Chainlit in the main process.
- `EasierlitClient`: starts your `run_func(app)` in one global worker.
- `EasierlitApp`: queue bridge for inbound user messages and outbound commands.

High-level flow:

1. `server.serve()` binds runtime and starts Chainlit.
2. Chainlit callback `on_message` converts input into `IncomingMessage`.
3. Worker calls `app.recv()` and handles message.
4. Worker returns output via `app.send(...)` or client CRUD APIs.

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


client = EasierlitClient(run_func=run_func, worker_mode="thread")
server = EasierlitServer(client=client)
server.serve()
```

Notes:

- `serve()` is blocking.
- `worker_mode` supports `"thread"` and `"process"`.
- In process mode, `run_func` and payloads must be picklable.

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

EasierlitClient(run_func, worker_mode="thread")

EasierlitApp.recv(timeout=None)
EasierlitApp.send(thread_id, content, author="Assistant", metadata=None)
EasierlitApp.update_message(thread_id, message_id, content, metadata=None)
EasierlitApp.delete_message(thread_id, message_id)
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

1. Long-running loop with `app.recv(timeout=...)`.
2. Handle `TimeoutError` as idle tick.
3. Break on `AppClosedError`.
4. Keep per-command exceptions contextual for logs.

If `run_func` raises uncaught exception:

- Easierlit logs traceback.
- Easierlit triggers server shutdown.
- Further incoming enqueue attempts are suppressed with shutdown messaging.

## 8. Thread CRUD in Worker

Available methods on `EasierlitClient`:

- `list_threads(first=20, cursor=None, search=None, user_identifier=None)`
- `get_thread(thread_id)`
- `update_thread(thread_id, name=None, metadata=None, tags=None)`
- `delete_thread(thread_id)`

Behavior details:

- Data layer is required for thread CRUD.
- If auth is configured, `update_thread` auto-resolves owner user and saves with `user_id`.
- In SQLite SQLAlchemyDataLayer, `tags` list is JSON-serialized on write and normalized to list on read.

## 9. Message CRUD and Fallback

Message methods:

- `app.send(...)`, `app.update_message(...)`, `app.delete_message(...)`
- `client.add_message(...)`, `client.update_message(...)`, `client.delete_message(...)`

Execution model:

1. If thread has active websocket session, message applies in realtime context.
2. If session inactive and data layer exists, Easierlit runs persistence fallback.
3. Fallback initializes internal HTTP Chainlit context before step CRUD.
4. If no session and no data layer, `ThreadSessionNotActiveError` is raised.

## 10. Creating Threads from run_func

Reference example: `examples/thread_create_in_run_func.py`

Pattern:

1. Generate new `thread_id` (for example with `uuid4`).
2. Call `client.update_thread(...)` to create/upsert thread metadata.
3. Call `client.add_message(...)` to add bootstrap assistant message.
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

Easierlit v0.1.0 mapping:

- Incoming `app.recv()` data is user-message flow.
- Outgoing `app.send()` and `client.add_message()` are assistant-message flow.
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

## 14. Release Checklist (v0.1.0)

```bash
python3 -m py_compile examples/*.py
python3 -m pytest
python3 -m build
python3 -m twine check dist/*
```

Also verify:

- `pyproject.toml` version is `0.1.0`
- README/doc links resolve (`README.md`, `README.ko.md`, `docs/usage.en.md`, `docs/usage.ko.md`, `docs/api-reference.en.md`, `docs/api-reference.ko.md`)
