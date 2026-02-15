# Easierlit

Easierlit is a thin wrapper over Chainlit for writing Python-first chat apps with a simple split:

- `EasierlitServer`: runs the Chainlit server in the main process.
- `EasierlitClient`: runs your app logic (`run_func`) in a worker.
- `EasierlitApp`: bridges incoming user messages and outgoing commands.

This README documents **Easierlit v0.1.0**.

## Install

```bash
pip install easierlit
```

For local development:

```bash
pip install -e .
```

## 60-Second Quick Start

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

## Core Concepts

- `run_func(app)` is your main loop.
- `app.recv()` blocks until a user message arrives.
- `app.send()` and related APIs emit assistant-side output.
- `server.serve()` is blocking and starts Chainlit headless.

Lifecycle summary:

`server.serve()` -> Chainlit callbacks -> `app.recv()` in worker -> `app.send()` / `client.*` CRUD

## Public API (v0.1.0)

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

## Auth and Persistence Defaults

- JWT secret is auto-managed at `.chainlit/jwt.secret`.
- Auth cookie name is fixed to `easierlit_access_token`.
- Default persistence is SQLite at `.chainlit/easierlit.db`.
- If SQLite schema is incompatible, Easierlit recreates it with backup.
- Sidebar default state is forced to `open`.

## Thread History Visibility

Chainlit shows Thread History when both conditions are true:

- `requireLogin=True`
- `dataPersistence=True`

In Easierlit, this usually means:

- set `auth=EasierlitAuthConfig(...)`
- keep persistence enabled (default)

## Message CRUD and Thread CRUD

Message APIs:

- `app.send(...)`
- `app.update_message(...)`
- `app.delete_message(...)`
- `client.add_message(...)`
- `client.update_message(...)`
- `client.delete_message(...)`

Thread APIs (via data layer):

- `client.list_threads(...)`
- `client.get_thread(thread_id)`
- `client.update_thread(...)`
- `client.delete_thread(thread_id)`

Important runtime behavior:

- With auth configured, `client.update_thread(...)` auto-assigns ownership to the auth user.
- For SQLite SQLAlchemyDataLayer, Easierlit auto serializes/deserializes thread `tags`.
- If no active websocket session exists, Easierlit runs data-layer message fallback with internal HTTP context initialization.

## Worker Failure Policy

Easierlit is fail-fast:

- If `run_func` raises, server shutdown is triggered immediately.
- UI receives a short summary when possible.
- Full traceback is logged on the server side.

## Message vs Tool Call in Chainlit

Chainlit distinguishes these at the step type level.

Message types:

- `user_message`
- `assistant_message`
- `system_message`

Tool/run types include:

- `tool`
- `run`
- `llm`
- `retrieval`
- `embedding`
- `rerank`

Easierlit v0.1.0 behavior:

- `app.recv()` consumes user-message flow.
- `app.send()` and `client.add_message()` produce assistant-message flow.
- Easierlit public API does **not** expose a dedicated tool-call step creation API yet.

UI rendering note (Chainlit): `ui.cot` supports `full`, `tool_call`, `hidden`.

## Example Map

- `examples/minimal.py`: basic echo bot.
- `examples/custom_auth.py`: single-account auth setup.
- `examples/thread_crud.py`: list/get/update/delete thread flow.
- `examples/thread_create_in_run_func.py`: create a new thread from `run_func`.

## Documentation

- API reference (EN, method-level contracts): `docs/api-reference.en.md`
- API reference (KO): `docs/api-reference.ko.md`
- Detailed guide (EN): `docs/usage.en.md`
- Korean overview: `README.ko.md`
- Detailed guide (KO): `docs/usage.ko.md`

For exact method contracts (parameters, returns, raises, failure modes), use API Reference first.

## Migration Notes

Removed APIs from older drafts are not part of v0.1.0 public usage.
Use only the APIs documented above.
