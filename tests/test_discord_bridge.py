from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import NAMESPACE_DNS, uuid5

from easierlit.discord_bridge import EasierlitDiscordBridge
from easierlit.models import OutgoingCommand
from easierlit.settings import EasierlitAuthConfig


@dataclass
class _FakePersistedUser:
    id: str
    identifier: str = "owner"


class _FakeDataLayer:
    def __init__(
        self,
        *,
        existing_user: _FakePersistedUser | None = None,
        created_user_id: str = "created-user-id",
        fail_update_thread: bool = False,
    ) -> None:
        self.existing_user = existing_user
        self.created_user_id = created_user_id
        self.fail_update_thread = fail_update_thread

        self.get_user_calls: list[str] = []
        self.create_user_calls: list[object] = []
        self.update_thread_calls: list[dict] = []

    async def get_user(self, identifier: str):
        self.get_user_calls.append(identifier)
        return self.existing_user

    async def create_user(self, user):
        self.create_user_calls.append(user)
        self.existing_user = _FakePersistedUser(
            id=self.created_user_id,
            identifier=getattr(user, "identifier", "owner"),
        )
        return self.existing_user

    async def update_thread(self, **kwargs):
        self.update_thread_calls.append(dict(kwargs))
        if self.fail_update_thread:
            raise RuntimeError("simulated update_thread failure")


class _FakeApp:
    def __init__(self) -> None:
        self.enqueue_calls: list[dict] = []

    def enqueue(self, **kwargs):
        self.enqueue_calls.append(dict(kwargs))
        return kwargs.get("message_id")


class _FakeRuntime:
    def __init__(self, *, app: _FakeApp | None, auth: EasierlitAuthConfig | None = None) -> None:
        self._app = app
        self._auth = auth
        self.discord_sender = None
        self.discord_typing_sender = None
        self.registered_channels: list[tuple[str, int]] = []

    def get_app(self):
        return self._app

    def get_auth(self):
        return self._auth

    def register_discord_channel(self, thread_id: str, channel_id: int) -> None:
        self.registered_channels.append((thread_id, channel_id))

    def set_discord_sender(self, sender):
        self.discord_sender = sender

    def set_discord_typing_state_sender(self, sender):
        self.discord_typing_sender = sender


class _FakeTypingCtx:
    def __init__(self, channel) -> None:
        self._channel = channel

    async def __aenter__(self):
        self._channel.typing_enters += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._channel.typing_exits += 1
        return False


class _FakeChannel:
    def __init__(self, *, channel_id: int, name: str = "general") -> None:
        self.id = channel_id
        self.name = name
        self.typing_enters = 0
        self.typing_exits = 0

    def typing(self):
        return _FakeTypingCtx(self)


class _FakeDiscordClient:
    def __init__(self) -> None:
        self.listeners: dict[str, object] = {}
        self.started_tokens: list[str] = []
        self.closed = False
        self._close_event = asyncio.Event()
        self.channels: dict[int, object] = {}
        self.user = SimpleNamespace(id=999, name="bridge-bot")

    def add_listener(self, listener, name: str):
        self.listeners[name] = listener

    async def start(self, token: str) -> None:
        self.started_tokens.append(token)
        await self._close_event.wait()

    async def close(self) -> None:
        self.closed = True
        self._close_event.set()

    def is_closed(self) -> bool:
        return self.closed

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        return self.channels.get(channel_id)


class _TestableBridge(EasierlitDiscordBridge):
    def __init__(self, *, runtime, bot_token: str, fake_client: _FakeDiscordClient) -> None:
        super().__init__(runtime=runtime, bot_token=bot_token)
        self._fake_client = fake_client

    def _create_discord_client(self):
        self._fake_client.add_listener(self._on_discord_ready, "on_ready")
        self._fake_client.add_listener(self._on_discord_message, "on_message")
        return self._fake_client


@dataclass
class _FakeAttachment:
    id: int
    filename: str
    url: str
    content_type: str | None = None
    size: int | None = None


@dataclass
class _FakeAuthor:
    id: int
    bot: bool
    display_name: str
    name: str


@dataclass
class _FakeGuild:
    id: int


@dataclass
class _FakeMessage:
    id: int
    author: _FakeAuthor
    channel: object
    content: str
    attachments: list[_FakeAttachment]
    guild: _FakeGuild | None = None
    created_at: datetime | None = None


def _build_bridge(
    *,
    existing_owner_user: _FakePersistedUser | None = None,
    created_user_id: str = "created-owner-id",
    fail_update_thread: bool = False,
    auth: EasierlitAuthConfig | None = None,
):
    app = _FakeApp()
    runtime = _FakeRuntime(
        app=app,
        auth=auth
        or EasierlitAuthConfig(
            username="admin",
            password="admin",
            identifier="admin-owner",
            metadata={"role": "owner"},
        ),
    )
    fake_client = _FakeDiscordClient()
    data_layer = _FakeDataLayer(
        existing_user=existing_owner_user,
        created_user_id=created_user_id,
        fail_update_thread=fail_update_thread,
    )
    bridge = _TestableBridge(runtime=runtime, bot_token="discord-token", fake_client=fake_client)
    return bridge, runtime, app, data_layer, fake_client


def test_start_stop_registers_callbacks_and_client_lifecycle(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, runtime, _, data_layer, fake_client = _build_bridge()
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        await bridge.start()
        assert runtime.discord_sender is not None
        assert runtime.discord_typing_sender is not None
        assert "on_ready" in fake_client.listeners
        assert "on_message" in fake_client.listeners

        await asyncio.sleep(0)
        assert fake_client.started_tokens == ["discord-token"]

        await bridge.stop()
        assert fake_client.closed is True
        assert runtime.discord_sender is None
        assert runtime.discord_typing_sender is None

    asyncio.run(_scenario())


def test_bot_message_is_ignored(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, runtime, app, data_layer, _ = _build_bridge()
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        message = _FakeMessage(
            id=101,
            author=_FakeAuthor(id=22, bot=True, display_name="bot", name="bot"),
            channel=_FakeChannel(channel_id=333, name="ops"),
            content="ignored",
            attachments=[],
            created_at=datetime.now(timezone.utc),
        )
        await bridge._on_discord_message(message)

        assert runtime.registered_channels == []
        assert app.enqueue_calls == []
        assert data_layer.update_thread_calls == []

    asyncio.run(_scenario())


def test_message_upserts_owner_thread_and_enqueues_payload(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        owner_user = _FakePersistedUser(id="owner-user-id", identifier="admin-owner")
        bridge, runtime, app, data_layer, _ = _build_bridge(existing_owner_user=owner_user)
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        channel = _FakeChannel(channel_id=777, name="alerts")
        message = _FakeMessage(
            id=2024,
            author=_FakeAuthor(
                id=55,
                bot=False,
                display_name="Dongju",
                name="dongju",
            ),
            channel=channel,
            content="hello from discord",
            attachments=[
                _FakeAttachment(
                    id=1,
                    filename="image.png",
                    url="https://example.com/image.png",
                    content_type="image/png",
                    size=123,
                )
            ],
            guild=_FakeGuild(id=9898),
            created_at=datetime(2026, 2, 20, 8, 1, 2, tzinfo=timezone.utc),
        )

        await bridge._on_discord_message(message)

        expected_thread_id = str(uuid5(NAMESPACE_DNS, "discord-channel:777"))
        assert runtime.registered_channels == [(expected_thread_id, 777)]

        assert len(data_layer.update_thread_calls) == 1
        thread_call = data_layer.update_thread_calls[0]
        assert thread_call["thread_id"] == expected_thread_id
        assert thread_call["name"] == "alerts"
        assert thread_call["user_id"] == "owner-user-id"
        assert thread_call["metadata"]["client_type"] == "discord"
        assert thread_call["metadata"]["clientType"] == "discord"
        assert thread_call["metadata"]["easierlit_discord_owner_id"] == "55"
        assert thread_call["metadata"]["easierlit_discord_channel_id"] == "777"
        assert thread_call["metadata"]["easierlit_discord_scope"] == "channel_id"

        assert len(app.enqueue_calls) == 1
        enqueue_call = app.enqueue_calls[0]
        assert enqueue_call["thread_id"] == expected_thread_id
        assert enqueue_call["session_id"] == "discord:55"
        assert enqueue_call["message_id"] == "discord:2024"
        assert enqueue_call["author"] == "Dongju"
        assert enqueue_call["content"] == "hello from discord"
        assert enqueue_call["created_at"] == "2026-02-20T08:01:02+00:00"
        assert enqueue_call["metadata"]["easierlit_discord_message_id"] == "2024"
        assert enqueue_call["metadata"]["easierlit_discord_guild_id"] == "9898"
        assert enqueue_call["elements"] == [
            {
                "id": "discord-att:1",
                "name": "image.png",
                "url": "https://example.com/image.png",
                "mime": "image/png",
                "size": "123",
                "type": "image",
                "display": "inline",
            }
        ]

    asyncio.run(_scenario())


def test_owner_user_is_created_when_missing(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, _, _, data_layer, _ = _build_bridge(
            existing_owner_user=None,
            created_user_id="fresh-user-id",
        )
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        message = _FakeMessage(
            id=10,
            author=_FakeAuthor(id=7, bot=False, display_name="U", name="u"),
            channel=_FakeChannel(channel_id=8, name="chat"),
            content="x",
            attachments=[],
            created_at=datetime.now(timezone.utc),
        )
        await bridge._on_discord_message(message)

        assert data_layer.get_user_calls == ["admin-owner"]
        assert len(data_layer.create_user_calls) == 1
        assert data_layer.update_thread_calls[0]["user_id"] == "fresh-user-id"

    asyncio.run(_scenario())


def test_attachment_only_message_is_enqueued(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, _, app, data_layer, _ = _build_bridge()
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        message = _FakeMessage(
            id=404,
            author=_FakeAuthor(id=88, bot=False, display_name="file-user", name="file-user"),
            channel=_FakeChannel(channel_id=1818, name="uploads"),
            content="",
            attachments=[
                _FakeAttachment(
                    id=9,
                    filename="report.pdf",
                    url="https://example.com/report.pdf",
                    content_type="application/pdf",
                )
            ],
            created_at=datetime.now(timezone.utc),
        )
        await bridge._on_discord_message(message)

        assert len(app.enqueue_calls) == 1
        call = app.enqueue_calls[0]
        assert call["content"] == ""
        assert call["elements"][0]["type"] == "pdf"

    asyncio.run(_scenario())


def test_sender_callback_uses_send_discord_command(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, runtime, _, data_layer, fake_client = _build_bridge()
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        captured: dict[str, object] = {}

        async def _fake_send_discord_command(*, client, channel_id, command, logger):
            captured["client"] = client
            captured["channel_id"] = channel_id
            captured["command"] = command
            return True

        monkeypatch.setattr(module, "send_discord_command", _fake_send_discord_command)

        await bridge.start()
        assert runtime.discord_sender is not None

        result = await runtime.discord_sender(
            321,
            OutgoingCommand(command="add_message", thread_id="t1", content="hello"),
        )
        assert result is True
        assert captured["client"] is fake_client
        assert captured["channel_id"] == 321

        await bridge.stop()

    asyncio.run(_scenario())


def test_typing_open_close_controls_channel_tasks(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, runtime, _, data_layer, _ = _build_bridge()
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        typing_channel = _FakeChannel(channel_id=911, name="typing")

        async def _fake_resolve_discord_channel(*, client, channel_id, logger):
            assert channel_id == 911
            return typing_channel

        monkeypatch.setattr(module, "resolve_discord_channel", _fake_resolve_discord_channel)

        await bridge.start()
        bridge._typing_heartbeat_seconds = 0.01
        bridge._typing_retry_seconds = 0.01

        assert runtime.discord_typing_sender is not None
        opened = await runtime.discord_typing_sender(911, True)
        await asyncio.sleep(0.03)
        assert opened is True
        assert typing_channel.typing_enters > 0

        closed = await runtime.discord_typing_sender(911, False)
        assert closed is True
        assert 911 not in bridge._typing_tasks

        await bridge.stop()

    asyncio.run(_scenario())


def test_data_layer_failure_does_not_block_enqueue(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, _, app, data_layer, _ = _build_bridge(fail_update_thread=True)
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        message = _FakeMessage(
            id=500,
            author=_FakeAuthor(id=77, bot=False, display_name="stable", name="stable"),
            channel=_FakeChannel(channel_id=300, name="errors"),
            content="still enqueue",
            attachments=[],
            created_at=datetime.now(timezone.utc),
        )

        await bridge._on_discord_message(message)

        assert len(data_layer.update_thread_calls) == 1
        assert len(app.enqueue_calls) == 1
        assert app.enqueue_calls[0]["content"] == "still enqueue"

    asyncio.run(_scenario())


def test_start_is_idempotent(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, _, _, data_layer, fake_client = _build_bridge()
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        await bridge.start()
        await bridge.start()
        await asyncio.sleep(0)
        assert fake_client.started_tokens == ["discord-token"]
        await bridge.stop()

    asyncio.run(_scenario())


def test_stop_cancels_typing_tasks(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, runtime, _, data_layer, _ = _build_bridge()
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        typing_channel = _FakeChannel(channel_id=222, name="typing-stop")

        async def _fake_resolve_discord_channel(*, client, channel_id, logger):
            return typing_channel

        monkeypatch.setattr(module, "resolve_discord_channel", _fake_resolve_discord_channel)

        await bridge.start()
        bridge._typing_heartbeat_seconds = 0.01
        bridge._typing_retry_seconds = 0.01

        assert runtime.discord_typing_sender is not None
        opened = await runtime.discord_typing_sender(222, True)
        assert opened is True
        await asyncio.sleep(0.02)
        assert 222 in bridge._typing_tasks

        await bridge.stop()
        assert bridge._typing_tasks == {}

    asyncio.run(_scenario())


def test_typing_close_without_open_returns_false(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, runtime, _, data_layer, _ = _build_bridge()
        monkeypatch.setattr(module, "get_data_layer", lambda: data_layer)

        await bridge.start()
        assert runtime.discord_typing_sender is not None
        closed = await runtime.discord_typing_sender(999, False)
        assert closed is False
        await bridge.stop()

    asyncio.run(_scenario())


def test_data_layer_missing_still_enqueues(monkeypatch):
    async def _scenario():
        from easierlit import discord_bridge as module

        bridge, runtime, app, _, _ = _build_bridge()
        monkeypatch.setattr(module, "get_data_layer", lambda: None)

        message = _FakeMessage(
            id=606,
            author=_FakeAuthor(id=42, bot=False, display_name="no-dl", name="no-dl"),
            channel=_FakeChannel(channel_id=17, name="fallback"),
            content="works without data layer",
            attachments=[],
            created_at=datetime.now(timezone.utc),
        )
        await bridge._on_discord_message(message)

        assert len(runtime.registered_channels) == 1
        assert len(app.enqueue_calls) == 1
        assert app.enqueue_calls[0]["content"] == "works without data layer"

    asyncio.run(_scenario())
