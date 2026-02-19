import asyncio
import os

import pytest
from chainlit.config import config

import easierlit.chainlit_entry as chainlit_entry
from easierlit import EasierlitApp, EasierlitAuthConfig, EasierlitClient
from easierlit.chainlit_entry import _apply_runtime_configuration
from easierlit.runtime import get_runtime


@pytest.fixture(autouse=True)
def _reset_state():
    runtime = get_runtime()
    runtime.unbind()

    previous_database_url = os.environ.get("DATABASE_URL")
    previous_literal_api_key = os.environ.get("LITERAL_API_KEY")

    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._DEFAULT_DATA_LAYER_REGISTERED = False
    config.code.password_auth_callback = None
    config.code.data_layer = None

    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("LITERAL_API_KEY", None)

    yield

    runtime.unbind()
    chainlit_entry._CONFIG_APPLIED = False
    chainlit_entry._DEFAULT_DATA_LAYER_REGISTERED = False
    config.code.password_auth_callback = None
    config.code.data_layer = None

    if previous_database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous_database_url

    if previous_literal_api_key is None:
        os.environ.pop("LITERAL_API_KEY", None)
    else:
        os.environ["LITERAL_API_KEY"] = previous_literal_api_key


def test_auth_config_sets_password_callback():
    auth = EasierlitAuthConfig(
        username="admin",
        password="admin",
        metadata={"role": "admin"},
    )
    runtime = get_runtime()
    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None]),
        app=EasierlitApp(),
        auth=auth,
    )

    _apply_runtime_configuration()
    assert config.code.password_auth_callback is not None

    result = asyncio.run(config.code.password_auth_callback("admin", "admin"))
    assert result is not None
    assert result.identifier == "admin"
    assert result.metadata == {"role": "admin"}

    invalid = asyncio.run(config.code.password_auth_callback("admin", "wrong"))
    assert invalid is None


def test_auth_config_uses_custom_identifier():
    auth = EasierlitAuthConfig(
        username="admin",
        password="admin",
        identifier="owner",
    )
    runtime = get_runtime()
    runtime.bind(
        client=EasierlitClient(on_message=lambda _app, _incoming: None, run_funcs=[lambda _app: None]),
        app=EasierlitApp(),
        auth=auth,
    )

    _apply_runtime_configuration()
    result = asyncio.run(config.code.password_auth_callback("admin", "admin"))
    assert result is not None
    assert result.identifier == "owner"


def test_auth_config_rejects_empty_credentials():
    with pytest.raises(ValueError):
        EasierlitAuthConfig(username="", password="admin")

    with pytest.raises(ValueError):
        EasierlitAuthConfig(username="admin", password="   ")


def test_auth_config_does_not_accept_jwt_secret_argument():
    with pytest.raises(TypeError):
        EasierlitAuthConfig(username="admin", password="admin", jwt_secret="x")  # type: ignore[call-arg]
