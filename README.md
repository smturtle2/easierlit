[English](README.md) | [한국어](README.ko.md)

# Easierlit

[![Version](https://img.shields.io/badge/version-0.3.0-2563eb)](pyproject.toml)
[![Python](https://img.shields.io/badge/python-3.10%2B-0ea5e9)](pyproject.toml)
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
- dedicated auth cookie (`easierlit_access_token`)
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

        app.send(
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

        app.send(
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

## Public API (v0.3.0)

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
EasierlitApp.send(thread_id, content, author="Assistant", metadata=None)
EasierlitApp.add_message(thread_id, content, author="Assistant", metadata=None)
EasierlitApp.update_message(thread_id, message_id, content, metadata=None)
EasierlitApp.delete_message(thread_id, message_id)
EasierlitApp.list_threads(first=20, cursor=None, search=None, user_identifier=None)
EasierlitApp.get_thread(thread_id)
EasierlitApp.new_thread(thread_id, name=None, metadata=None, tags=None)
EasierlitApp.update_thread(thread_id, name=None, metadata=None, tags=None)
EasierlitApp.delete_thread(thread_id)
EasierlitApp.close()

EasierlitAuthConfig(username, password, identifier=None, metadata=None)
EasierlitPersistenceConfig(enabled=True, sqlite_path=".chainlit/easierlit.db")
```

For exact method contracts, use:

- `docs/api-reference.en.md`

This includes parameter constraints, return semantics, exceptions, side effects, concurrency notes, and failure-mode fixes for each public method.

## Auth and Persistence Defaults

- JWT secret: auto-managed at `.chainlit/jwt.secret`
- Auth cookie: `easierlit_access_token`
- Default persistence: SQLite at `.chainlit/easierlit.db`
- If SQLite schema is incompatible, Easierlit recreates DB with backup
- Sidebar default state is forced to `open`

Thread History sidebar visibility follows Chainlit policy:

- `requireLogin=True`
- `dataPersistence=True`

Typical Easierlit setup:

- set `auth=EasierlitAuthConfig(...)`
- keep persistence enabled (default)

## Message and Thread Operations

Message APIs:

- `app.send(...)`
- `app.add_message(...)`
- `app.update_message(...)`
- `app.delete_message(...)`

Thread APIs:

- `app.list_threads(...)`
- `app.get_thread(thread_id)`
- `app.new_thread(...)`
- `app.update_thread(...)`
- `app.delete_thread(thread_id)`

Behavior highlights:

- `app.new_thread(...)` creates only when thread does not exist.
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

Easierlit v0.3.0 currently provides message-centric public APIs.
A dedicated tool-call step creation public API is not provided yet.

## Example Map

- `examples/minimal.py`: basic echo bot
- `examples/custom_auth.py`: single-account auth
- `examples/thread_crud.py`: thread list/get/update/delete
- `examples/thread_create_in_run_func.py`: create thread from `run_func`

## Documentation Map

- Method-level API contracts (EN): `docs/api-reference.en.md`
- Method-level API contracts (KO): `docs/api-reference.ko.md`
- Full usage guide (EN): `docs/usage.en.md`
- Full usage guide (KO): `docs/usage.ko.md`

## Migration Note

Removed APIs from earlier drafts are not part of v0.3.0 public usage.
Use the APIs documented in this README and API Reference.
