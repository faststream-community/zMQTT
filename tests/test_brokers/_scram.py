"""Test-only SCRAM-SHA-256 client (RFC 5802 / RFC 7677)."""

import base64
import hashlib
import hmac
import secrets

SCRAM_USERNAME = "zmqtt-scram"
SCRAM_PASSWORD = "zmqtt-scram-password"  # noqa: S105 - disposable test credential
SCRAM_HOST = "127.0.0.1"
SCRAM_PORT = 1889


def _attributes(data: bytes | None) -> dict[str, str]:
    if data is None:
        msg = "Missing SCRAM data"
        raise ValueError(msg)
    attributes: dict[str, str] = {}
    for field in data.decode("ascii").split(","):
        key, separator, value = field.partition("=")
        if not separator or len(key) != 1 or key in attributes or key == "m":
            msg = "Invalid SCRAM attributes"
            raise ValueError(msg)
        attributes[key] = value
    return attributes


class ScramSHA256Handler:
    method = "SCRAM-SHA-256"

    def __init__(self, username: str = SCRAM_USERNAME, password: str = SCRAM_PASSWORD) -> None:
        self.username = username
        self.password = password
        self.calls: list[str] = []
        self.nonces: list[str] = []
        self.verified = 0
        self._first_bare = b""
        self._server_signature: bytes | None = None

    def begin_exchange(self) -> bytes:
        nonce = secrets.token_urlsafe(24)
        self.nonces.append(nonce)
        self._server_signature = None
        username = self.username.replace("=", "=3D").replace(",", "=2C")
        self._first_bare = f"n={username},r={nonce}".encode("ascii")
        return b"n,," + self._first_bare

    async def initial_data(self) -> bytes:
        self.calls.append("initial")
        return self.begin_exchange()

    async def continue_data(self, data: bytes | None) -> bytes:
        self.calls.append("continue")
        attributes = _attributes(data)
        if not {"r", "s", "i"} <= attributes.keys() or not self.nonces:
            msg = "Incomplete SCRAM challenge"
            raise ValueError(msg)
        nonce = attributes["r"]
        if not nonce.startswith(self.nonces[-1]) or len(nonce) <= len(self.nonces[-1]):
            msg = "Invalid SCRAM server nonce"
            raise ValueError(msg)
        iterations = int(attributes["i"])
        if not 1 <= iterations <= 1_000_000:
            msg = "Invalid SCRAM iteration count"
            raise ValueError(msg)
        salt = base64.b64decode(attributes["s"], validate=True)
        salted_password = hashlib.pbkdf2_hmac("sha256", self.password.encode("utf8"), salt, iterations)
        client_key = hmac.digest(salted_password, b"Client Key", "sha256")
        stored_key = hashlib.sha256(client_key).digest()
        final_bare = f"c=biws,r={nonce}".encode("ascii")
        assert data is not None
        auth_message = b",".join((self._first_bare, data, final_bare))
        client_signature = hmac.digest(stored_key, auth_message, "sha256")
        proof = bytes(a ^ b for a, b in zip(client_key, client_signature, strict=True))
        server_key = hmac.digest(salted_password, b"Server Key", "sha256")
        self._server_signature = hmac.digest(server_key, auth_message, "sha256")
        return final_bare + b",p=" + base64.b64encode(proof)

    async def finalize_data(self, data: bytes | None) -> None:
        self.calls.append("finalize")
        attributes = _attributes(data)
        if "e" in attributes or "v" not in attributes or self._server_signature is None:
            msg = "Missing SCRAM server signature"
            raise ValueError(msg)
        signature = base64.b64decode(attributes["v"], validate=True)
        if not hmac.compare_digest(signature, self._server_signature):
            msg = "Invalid SCRAM server signature"
            raise ValueError(msg)
        self.verified += 1
        self._server_signature = None
