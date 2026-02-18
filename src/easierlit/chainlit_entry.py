from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys
from pathlib import Path

import chainlit as cl
from chainlit.auth import require_login
from chainlit.config import config
from chainlit.data import get_data_layer
from chainlit.server import app as chainlit_app
from chainlit.user import User
from fastapi import HTTPException
from fastapi.responses import FileResponse

# When this module is loaded by Chainlit's load_module(file_path), ensure the
# src root is importable so absolute imports keep working.
_THIS_FILE = Path(__file__).resolve()
_SRC_ROOT = _THIS_FILE.parents[1]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from easierlit.discord_bridge import EasierlitDiscordBridge
from easierlit.errors import AppClosedError, RunFuncExecutionError
from easierlit.models import IncomingMessage
from easierlit.runtime import get_runtime
from easierlit.settings import (
    EasierlitPersistenceConfig,
    assert_local_storage_operational,
    ensure_local_storage_provider,
)
from easierlit.storage.local import LOCAL_STORAGE_ROUTE_PREFIX
from easierlit.sqlite_bootstrap import ensure_sqlite_schema

LOGGER = logging.getLogger(__name__)
RUNTIME = get_runtime()
_CONFIG_APPLIED = False
_APP_CLOSED_WARNING_EMITTED = False
_WORKER_FAILURE_UI_NOTIFIED = False
_DISCORD_BRIDGE: EasierlitDiscordBridge | None = None
_DEFAULT_DATA_LAYER_REGISTERED = False
_LOCAL_STORAGE_ROUTE_REGISTERED = False


def _summarize_worker_error(traceback_text: str) -> str:
    lines = [line.strip() for line in traceback_text.strip().splitlines() if line.strip()]
    if lines:
        return lines[-1]
    return "Unknown run_func error"


def _register_discord_channel_for_current_session() -> None:
    session = cl.context.session
    if getattr(session, "client_type", None) != "discord":
        return

    try:
        channel = cl.user_session.get("discord_channel")
    except Exception:
        return

    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return

    try:
        resolved_channel_id = int(channel_id)
    except (TypeError, ValueError):
        return

    RUNTIME.register_discord_channel(
        thread_id=session.thread_id,
        channel_id=resolved_channel_id,
    )


def _register_non_discord_session_for_current_session() -> None:
    session = cl.context.session
    if getattr(session, "client_type", None) == "discord":
        return
    RUNTIME.register_session(thread_id=session.thread_id, session_id=session.id)


def _apply_runtime_configuration() -> None:
    global _CONFIG_APPLIED
    if _CONFIG_APPLIED:
        return

    _register_local_storage_route_if_needed()
    _apply_auth_configuration()
    _register_default_data_layer_if_needed()

    # Easierlit policy.
    config.ui.default_sidebar_state = "open"
    config.ui.cot = "full"
    _CONFIG_APPLIED = True


def _register_local_storage_route_if_needed() -> None:
    global _LOCAL_STORAGE_ROUTE_REGISTERED
    if _LOCAL_STORAGE_ROUTE_REGISTERED:
        return

    route_path = f"{LOCAL_STORAGE_ROUTE_PREFIX}" + "/{object_key:path}"

    @chainlit_app.get(route_path)
    async def _easierlit_local_storage_file(object_key: str):
        persistence = RUNTIME.get_persistence()
        if persistence is None:
            raise HTTPException(status_code=404, detail="Local storage is not configured.")

        try:
            storage_provider = ensure_local_storage_provider(persistence.storage_provider)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail="Local storage provider is unavailable.",
            ) from exc

        try:
            file_path = storage_provider.resolve_file_path(object_key)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found.")

        return FileResponse(path=str(file_path))

    _LOCAL_STORAGE_ROUTE_REGISTERED = True


def _apply_auth_configuration() -> None:
    auth = RUNTIME.get_auth()
    if auth is None:
        return

    expected_username = auth.username
    expected_password = auth.password
    resolved_identifier = auth.identifier or auth.username
    resolved_metadata = auth.metadata or {}

    async def _password_auth_callback(username: str, password: str) -> User | None:
        if not secrets.compare_digest(username, expected_username):
            return None
        if not secrets.compare_digest(password, expected_password):
            return None
        return User(identifier=resolved_identifier, metadata=dict(resolved_metadata))

    cl.password_auth_callback(_password_auth_callback)


def _should_register_default_data_layer() -> bool:
    persistence = RUNTIME.get_persistence() or EasierlitPersistenceConfig()
    if not persistence.enabled:
        return False
    if config.code.data_layer is not None:
        return False
    if os.getenv("DATABASE_URL"):
        return False
    if os.getenv("LITERAL_API_KEY"):
        return False
    return True


def _register_default_data_layer_if_needed() -> None:
    global _DEFAULT_DATA_LAYER_REGISTERED
    if not _should_register_default_data_layer():
        _DEFAULT_DATA_LAYER_REGISTERED = False
        return

    persistence = RUNTIME.get_persistence() or EasierlitPersistenceConfig()
    db_path = ensure_sqlite_schema(persistence.sqlite_path).resolve()
    conninfo = f"sqlite+aiosqlite:///{db_path}"
    storage_provider = ensure_local_storage_provider(persistence.storage_provider)

    @cl.data_layer
    def _easierlit_default_data_layer():
        from chainlit.data.sql_alchemy import SQLAlchemyDataLayer

        return SQLAlchemyDataLayer(conninfo=conninfo, storage_provider=storage_provider)

    _DEFAULT_DATA_LAYER_REGISTERED = True
    LOGGER.info("Easierlit default SQLite data layer enabled at %s", db_path)


async def _start_discord_bridge_if_needed() -> None:
    global _DISCORD_BRIDGE

    discord_token = RUNTIME.get_discord_token()
    if discord_token is None:
        return

    if _DISCORD_BRIDGE is None:
        _DISCORD_BRIDGE = EasierlitDiscordBridge(runtime=RUNTIME, bot_token=discord_token)

    try:
        await _DISCORD_BRIDGE.start()
    except Exception:
        LOGGER.exception("Failed to start Easierlit Discord bridge.")
        await _DISCORD_BRIDGE.stop()
        _DISCORD_BRIDGE = None


async def _stop_discord_bridge_if_running() -> None:
    global _DISCORD_BRIDGE

    bridge = _DISCORD_BRIDGE
    _DISCORD_BRIDGE = None
    if bridge is None:
        return

    try:
        await bridge.stop()
    except Exception:
        LOGGER.exception("Failed to stop Easierlit Discord bridge cleanly.")


@cl.on_app_startup
async def _on_app_startup() -> None:
    global _APP_CLOSED_WARNING_EMITTED, _WORKER_FAILURE_UI_NOTIFIED
    _apply_runtime_configuration()
    _APP_CLOSED_WARNING_EMITTED = False
    _WORKER_FAILURE_UI_NOTIFIED = False
    RUNTIME.set_main_loop(asyncio.get_running_loop())
    await RUNTIME.start_dispatcher()
    await _start_discord_bridge_if_needed()

    try:
        data_layer = get_data_layer()
    except Exception:
        LOGGER.exception("Failed to initialize Chainlit data layer at startup.")
        data_layer = None

    if _DEFAULT_DATA_LAYER_REGISTERED and data_layer is not None:
        storage_provider = getattr(data_layer, "storage_provider", None)
        ensure_local_storage_provider(storage_provider)
        await assert_local_storage_operational(storage_provider)

    if not require_login():
        LOGGER.warning(
            "Thread History sidebar is hidden by Chainlit policy because "
            "requireLogin=False. Configure Easierlit auth to enable it."
        )
    if data_layer is None:
        LOGGER.warning(
            "Thread History sidebar may be hidden because dataPersistence=False. "
            "Configure a data layer (or keep Easierlit default persistence enabled)."
        )


@cl.on_app_shutdown
async def _on_app_shutdown() -> None:
    global _APP_CLOSED_WARNING_EMITTED, _CONFIG_APPLIED, _WORKER_FAILURE_UI_NOTIFIED
    global _DEFAULT_DATA_LAYER_REGISTERED

    await _stop_discord_bridge_if_running()
    await RUNTIME.stop_dispatcher()

    client = RUNTIME.get_client()
    if client is None:
        return

    try:
        client.stop()
    except RunFuncExecutionError as exc:
        worker_error = client.peek_worker_error()
        summary = _summarize_worker_error(worker_error or str(exc))
        LOGGER.warning("run_func crash acknowledged during shutdown: %s", summary)
    finally:
        _CONFIG_APPLIED = False
        _APP_CLOSED_WARNING_EMITTED = False
        _WORKER_FAILURE_UI_NOTIFIED = False
        _DEFAULT_DATA_LAYER_REGISTERED = False


@cl.on_chat_start
async def _on_chat_start() -> None:
    _register_non_discord_session_for_current_session()
    _register_discord_channel_for_current_session()


@cl.on_chat_resume
async def _on_chat_resume(_thread: dict) -> None:
    _register_non_discord_session_for_current_session()
    _register_discord_channel_for_current_session()


@cl.on_chat_end
async def _on_chat_end() -> None:
    session = cl.context.session
    RUNTIME.unregister_session(session.id)


@cl.on_message
async def _on_message(message: cl.Message) -> None:
    global _APP_CLOSED_WARNING_EMITTED, _WORKER_FAILURE_UI_NOTIFIED

    session = cl.context.session
    _register_non_discord_session_for_current_session()
    _register_discord_channel_for_current_session()

    incoming = IncomingMessage(
        thread_id=session.thread_id,
        session_id=session.id,
        message_id=message.id,
        content=message.content or "",
        author=message.author,
        created_at=message.created_at,
        metadata=message.metadata or {},
    )
    try:
        RUNTIME.enqueue_incoming(incoming)
    except AppClosedError:
        client = RUNTIME.get_client()
        worker_error = client.peek_worker_error() if client is not None else None
        if worker_error is None:
            raise

        summary = _summarize_worker_error(worker_error)
        if not _APP_CLOSED_WARNING_EMITTED:
            LOGGER.warning(
                "Worker app already closed after run_func crash; server shutdown in progress: %s",
                summary,
            )
            _APP_CLOSED_WARNING_EMITTED = True

        if not _WORKER_FAILURE_UI_NOTIFIED:
            try:
                await cl.Message(
                    content=(
                        "Internal worker error detected. Server is shutting down.\n"
                        f"Reason: {summary}"
                    ),
                    author="Easierlit",
                ).send()
                _WORKER_FAILURE_UI_NOTIFIED = True
            except Exception:
                LOGGER.exception("Failed to send worker crash summary message.")
