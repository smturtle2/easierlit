from __future__ import annotations

import asyncio

import pytest


class _FakeChainlitDiscordClient:
    def __init__(self) -> None:
        self.start_calls: list[tuple[tuple, dict]] = []

    async def start(self, *args, **kwargs):
        self.start_calls.append((args, kwargs))
        return "started"


def _reset_discord_autostart_state(module) -> None:
    module._CHAINLIT_DISCORD_CLIENT = None
    module._CHAINLIT_DISCORD_START_ORIGINAL = None
    module._CHAINLIT_DISCORD_AUTOSTART_SUPPRESSED = False


def test_suppress_and_restore_chainlit_discord_autostart(monkeypatch):
    import easierlit.chainlit_entry as module

    fake_client = _FakeChainlitDiscordClient()
    monkeypatch.setattr(module, "_resolve_chainlit_discord_client", lambda: fake_client)
    _reset_discord_autostart_state(module)

    module._suppress_chainlit_discord_autostart()
    assert module._CHAINLIT_DISCORD_AUTOSTART_SUPPRESSED is True
    assert callable(module._CHAINLIT_DISCORD_START_ORIGINAL)

    suppressed_result = asyncio.run(fake_client.start("token-a"))
    assert suppressed_result is None
    assert fake_client.start_calls == []

    module._restore_chainlit_discord_autostart()
    assert module._CHAINLIT_DISCORD_AUTOSTART_SUPPRESSED is False

    restored_result = asyncio.run(fake_client.start("token-b"))
    assert restored_result == "started"
    assert fake_client.start_calls == [(("token-b",), {})]


def test_suppress_restore_are_idempotent(monkeypatch):
    import easierlit.chainlit_entry as module

    fake_client = _FakeChainlitDiscordClient()
    monkeypatch.setattr(module, "_resolve_chainlit_discord_client", lambda: fake_client)
    _reset_discord_autostart_state(module)

    module._suppress_chainlit_discord_autostart()
    first_suppressed_start = fake_client.start
    module._suppress_chainlit_discord_autostart()
    second_suppressed_start = fake_client.start

    assert first_suppressed_start is second_suppressed_start
    assert module._CHAINLIT_DISCORD_AUTOSTART_SUPPRESSED is True

    module._restore_chainlit_discord_autostart()
    restored_start = fake_client.start
    module._restore_chainlit_discord_autostart()
    assert fake_client.start is restored_start
    assert module._CHAINLIT_DISCORD_AUTOSTART_SUPPRESSED is False


def test_on_app_startup_suppresses_before_bridge_start(monkeypatch):
    import easierlit.chainlit_entry as module

    events: list[str] = []

    monkeypatch.setattr(module, "_suppress_chainlit_discord_autostart", lambda: events.append("suppress"))
    monkeypatch.setattr(module, "_apply_runtime_configuration", lambda: events.append("apply_runtime"))
    monkeypatch.setattr(module.RUNTIME, "set_main_loop", lambda _loop: events.append("set_main_loop"))

    async def _fake_start_dispatcher():
        events.append("start_dispatcher")

    async def _fake_start_bridge():
        events.append("start_bridge")

    monkeypatch.setattr(module.RUNTIME, "start_dispatcher", _fake_start_dispatcher)
    monkeypatch.setattr(module, "_start_discord_bridge_if_needed", _fake_start_bridge)
    monkeypatch.setattr(module, "get_data_layer", lambda: object())
    monkeypatch.setattr(module, "_DEFAULT_DATA_LAYER_REGISTERED", False)
    monkeypatch.setattr(module, "require_login", lambda: True)

    asyncio.run(module._on_app_startup())

    assert "suppress" in events
    assert "start_bridge" in events
    assert events.index("suppress") < events.index("start_bridge")


def test_on_app_shutdown_restores_autostart(monkeypatch):
    import easierlit.chainlit_entry as module

    events: list[str] = []

    async def _fake_stop_bridge():
        events.append("stop_bridge")

    async def _fake_stop_dispatcher():
        events.append("stop_dispatcher")

    monkeypatch.setattr(module, "_stop_discord_bridge_if_running", _fake_stop_bridge)
    monkeypatch.setattr(module.RUNTIME, "stop_dispatcher", _fake_stop_dispatcher)
    monkeypatch.setattr(module, "_restore_chainlit_discord_autostart", lambda: events.append("restore"))
    monkeypatch.setattr(module.RUNTIME, "get_client", lambda: None)

    asyncio.run(module._on_app_shutdown())

    assert events == ["stop_bridge", "stop_dispatcher", "restore"]


def test_on_app_shutdown_restores_even_if_dispatcher_stop_fails(monkeypatch):
    import easierlit.chainlit_entry as module

    events: list[str] = []

    async def _fake_stop_bridge():
        events.append("stop_bridge")

    async def _failing_stop_dispatcher():
        events.append("stop_dispatcher")
        raise RuntimeError("dispatcher stop failed")

    monkeypatch.setattr(module, "_stop_discord_bridge_if_running", _fake_stop_bridge)
    monkeypatch.setattr(module.RUNTIME, "stop_dispatcher", _failing_stop_dispatcher)
    monkeypatch.setattr(module, "_restore_chainlit_discord_autostart", lambda: events.append("restore"))
    monkeypatch.setattr(module.RUNTIME, "get_client", lambda: None)

    with pytest.raises(RuntimeError):
        asyncio.run(module._on_app_shutdown())

    assert "restore" in events
