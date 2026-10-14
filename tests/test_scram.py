"""Independent checks for the test-only SCRAM client."""

import pytest

from tests.test_brokers._scram import ScramSHA256Handler

_NONCE = "rOprNGfwEbeRWgbNEkqO"
_SERVER_NONCE = _NONCE + "%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
_CHALLENGE = f"r={_SERVER_NONCE},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096".encode()
_FINAL = b"v=6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch) -> ScramSHA256Handler:
    monkeypatch.setattr("tests.test_brokers._scram.secrets.token_urlsafe", lambda _: _NONCE)
    return ScramSHA256Handler("user", "pencil")


async def test_scram_rfc7677_vector(handler: ScramSHA256Handler) -> None:
    # https://www.rfc-editor.org/rfc/rfc7677#section-3
    assert await handler.initial_data() == f"n,,n=user,r={_NONCE}".encode()
    assert await handler.continue_data(_CHALLENGE) == (
        f"c=biws,r={_SERVER_NONCE},p=dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=".encode()
    )
    await handler.finalize_data(_FINAL)
    assert handler.verified == 1
    handler.begin_exchange()
    with pytest.raises(ValueError, match="Missing SCRAM server signature"):
        await handler.finalize_data(_FINAL)


@pytest.mark.parametrize(
    "challenge",
    [
        None,
        b"r=wrong,s=c2FsdA==,i=4096",
        b"r=nonce",
        b"m=required",
        b"r=a,r=b",
        f"r={_NONCE},s=c2FsdA==,i=4096".encode(),
        f"r={_SERVER_NONCE},s=invalid!,i=4096".encode(),
        f"r={_SERVER_NONCE},s=c2FsdA==,i=0".encode(),
    ],
)
async def test_scram_rejects_invalid_challenge(handler: ScramSHA256Handler, challenge: bytes | None) -> None:
    await handler.initial_data()
    with pytest.raises(ValueError, match=r"SCRAM|base64|padding"):
        await handler.continue_data(challenge)


@pytest.mark.parametrize("final", [None, b"e=invalid-proof", b"v=AAAA", b"v=bad!", b"s=missing-signature"])
async def test_scram_requires_valid_server_proof(handler: ScramSHA256Handler, final: bytes | None) -> None:
    await handler.initial_data()
    await handler.continue_data(_CHALLENGE)
    with pytest.raises(ValueError, match=r"SCRAM|base64|padding"):
        await handler.finalize_data(final)
    assert handler.verified == 0
