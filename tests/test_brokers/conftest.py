"""Resources for live broker integration tests."""

import base64
import json
import time
import uuid
from collections.abc import Iterator
from http import HTTPStatus
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from tests.test_brokers._scram import SCRAM_PASSWORD

_API = "http://127.0.0.1:18083/api/v5"
_LISTENER = "/listeners/tcp%3Ascram"
_CHAIN = "/authentication"
_AUTHENTICATOR = _CHAIN + "/scram%3Abuilt_in_database"
_CONFIG = {
    "mechanism": "scram",
    "backend": "built_in_database",
    "algorithm": "sha256",
    "iteration_count": 4096,
    "enable": True,
    "precondition": "str_eq(listener, 'tcp:scram')",
}
_CREDENTIAL = base64.b64encode(b"zmqtt-test:zmqtt-test-secret").decode("ascii")


def _request(method: str, path: str, body: dict[str, object] | None = None) -> Any:  # noqa: ANN401
    request = Request(  # noqa: S310 - fixed local test API
        _API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Basic " + _CREDENTIAL, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - fixed local test API
            data = response.read()
    except HTTPError as error:
        error.msg = f"{error.reason}: {error.read().decode()}"
        raise
    return json.loads(data) if data else None


def _wait_for_listener() -> None:
    deadline = time.monotonic() + 60
    while True:
        try:
            listener = _request("GET", _LISTENER)
            break
        except HTTPError as error:
            if error.code != HTTPStatus.SERVICE_UNAVAILABLE:
                raise
        except (URLError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            msg = "EMQX Management API did not become ready within 60 seconds"
            raise TimeoutError(msg)
        time.sleep(0.5)
    if not listener["running"] or not listener["enable_authn"] or listener["bind"] != "0.0.0.0:1884":
        msg = "Unexpected EMQX SCRAM listener configuration"
        raise RuntimeError(msg)
    if _request("GET", "/listeners/tcp%3Adefault")["enable_authn"]:
        msg = "The ordinary EMQX listener must have authentication disabled"
        raise RuntimeError(msg)


@pytest.fixture(scope="session")
def scram_username() -> Iterator[str]:
    """Create a separate SCRAM user for each pytest worker and remove it afterward."""
    _wait_for_listener()
    authenticators = _request("GET", _CHAIN)
    if len(authenticators) != 1 or any(authenticators[0].get(key) != value for key, value in _CONFIG.items()):
        msg = "Unexpected EMQX authentication configuration; recreate the Compose service"
        raise RuntimeError(msg)
    username = "zmqtt-scram-" + uuid.uuid4().hex
    _request(
        "POST",
        _AUTHENTICATOR + "/users",
        {
            "user_id": username,
            "password": SCRAM_PASSWORD,
            "is_superuser": False,
        },
    )
    try:
        yield username
    finally:
        _request("DELETE", _AUTHENTICATOR + "/users/" + username)
