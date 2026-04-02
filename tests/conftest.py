import uuid

import pytest


@pytest.fixture
def topic() -> str:
    return f"/zmqtt/test/{uuid.uuid4()}"
