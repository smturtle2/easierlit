import pytest

from easierlit import EasierlitDiscordConfig


def test_discord_config_defaults():
    config = EasierlitDiscordConfig()

    assert config.enabled is True
    assert config.bot_token is None


def test_discord_config_allows_none_bot_token():
    config = EasierlitDiscordConfig(enabled=True, bot_token=None)

    assert config.enabled is True
    assert config.bot_token is None


def test_discord_config_rejects_blank_bot_token():
    with pytest.raises(ValueError):
        EasierlitDiscordConfig(enabled=True, bot_token="   ")
