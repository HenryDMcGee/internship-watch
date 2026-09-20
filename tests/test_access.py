import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.hashes import SHA256

from src.access import AccessTokenError, verify_access_token

TEAM_DOMAIN = "royal-band-14be.cloudflareaccess.com"
AUD = "test-audience"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_bytes(value: int) -> bytes:
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


@pytest.fixture(scope="module")
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def jwks(keypair):
    _, public_key = keypair
    numbers = public_key.public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "n": _b64url(_int_to_bytes(numbers.n)),
        "e": _b64url(_int_to_bytes(numbers.e)),
    }
    return {"keys": [jwk]}


def make_token(keypair, *, payload_overrides=None, kid="test-key"):
    private_key, _ = keypair
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    payload = {
        "aud": AUD,
        "iss": f"https://{TEAM_DOMAIN}",
        "email": "henry@example.com",
        "sub": "user-123",
        "exp": int(time.time()) + 3600,
    }
    payload.update(payload_overrides or {})
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(payload).encode())}"
    signature = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def test_valid_token_verifies(keypair, jwks):
    token = make_token(keypair)
    claims = verify_access_token(token, jwks, audience=AUD, team_domain=TEAM_DOMAIN)
    assert claims.email == "henry@example.com"
    assert claims.subject == "user-123"


def test_tampered_payload_is_rejected(keypair, jwks):
    token = make_token(keypair)
    header_b64, payload_b64, sig_b64 = token.split(".")
    tampered_payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    tampered_payload["email"] = "attacker@example.com"
    tampered_b64 = _b64url(json.dumps(tampered_payload).encode())
    tampered_token = f"{header_b64}.{tampered_b64}.{sig_b64}"
    with pytest.raises(AccessTokenError):
        verify_access_token(tampered_token, jwks, audience=AUD, team_domain=TEAM_DOMAIN)


def test_expired_token_is_rejected(keypair, jwks):
    token = make_token(keypair, payload_overrides={"exp": int(time.time()) - 10})
    with pytest.raises(AccessTokenError):
        verify_access_token(token, jwks, audience=AUD, team_domain=TEAM_DOMAIN)


def test_wrong_audience_is_rejected(keypair, jwks):
    token = make_token(keypair)
    with pytest.raises(AccessTokenError):
        verify_access_token(token, jwks, audience="someone-elses-app", team_domain=TEAM_DOMAIN)


def test_wrong_issuer_is_rejected(keypair, jwks):
    token = make_token(keypair, payload_overrides={"iss": "https://not-the-team.cloudflareaccess.com"})
    with pytest.raises(AccessTokenError):
        verify_access_token(token, jwks, audience=AUD, team_domain=TEAM_DOMAIN)


def test_unknown_kid_is_rejected(keypair, jwks):
    token = make_token(keypair, kid="some-other-key")
    with pytest.raises(AccessTokenError):
        verify_access_token(token, jwks, audience=AUD, team_domain=TEAM_DOMAIN)


def test_malformed_token_is_rejected(jwks):
    with pytest.raises(AccessTokenError):
        verify_access_token("not-a-jwt", jwks, audience=AUD, team_domain=TEAM_DOMAIN)
