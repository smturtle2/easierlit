import os

import pytest
from chainlit.config import config


@pytest.fixture(autouse=True)
def _force_chainlit_headless():
    previous_headless = config.run.headless
    previous_env = os.environ.get("CHAINLIT_HEADLESS")

    config.run.headless = True
    os.environ["CHAINLIT_HEADLESS"] = "true"

    try:
        yield
    finally:
        config.run.headless = previous_headless
        if previous_env is None:
            os.environ.pop("CHAINLIT_HEADLESS", None)
        else:
            os.environ["CHAINLIT_HEADLESS"] = previous_env
