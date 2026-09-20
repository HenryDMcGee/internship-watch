"""Verify a Cloudflare Access login token, with no dependency beyond the
standard library so it runs on Pyodide (Python Workers only support
packages built for Pyodide; PyJWT's usual `cryptography` backend is not
one of them).

Cloudflare Access puts a signed RS256 JWT in the `Cf-Access-Jwt-Assertion`
header on every request that reaches the origin. We check its signature
against the team's published JWKS, plus the audience and expiry.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass

ACCESS_HEADER = "Cf-Access-Jwt-Assertion"

# DER prefix for "this is a SHA-256 digest" in a PKCS#1 v1.5 signature,
# per RFC 8017 appendix A.2.4. Fixed for a fixed hash algorithm.
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class AccessTokenError(Exception):
    """The Access token is missing, malformed, expired, or doesn't verify."""


@dataclass
class AccessClaims:
    email: str | None
    subject: str | None
    expires_at: int | None


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _jwk_int(value: str) -> int:
    return int.from_bytes(_b64url_decode(value), "big")


def verify_rs256_signature(signing_input: bytes, signature: bytes, modulus_n: int, exponent_e: int) -> bool:
    """Verify a PKCS#1 v1.5 RSA/SHA-256 signature using only `pow` for modexp."""
    key_size = (modulus_n.bit_length() + 7) // 8
    if len(signature) != key_size:
        return False
    signature_int = int.from_bytes(signature, "big")
    decrypted_int = pow(signature_int, exponent_e, modulus_n)
    decrypted = decrypted_int.to_bytes(key_size, "big")

    digest = hashlib.sha256(signing_input).digest()
    expected_suffix = _SHA256_DIGEST_INFO_PREFIX + digest
    padding_len = key_size - 3 - len(expected_suffix)
    if padding_len < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * padding_len + b"\x00" + expected_suffix
    return decrypted == expected


def decode_jwt_unverified(token: str) -> tuple[dict, dict, bytes, bytes]:
    """Split a compact JWT into (header, payload, signing_input, signature)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise AccessTokenError("token does not have three parts")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AccessTokenError(f"could not decode token: {exc}") from exc
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    return header, payload, signing_input, signature


def find_jwk(jwks: dict, kid: str) -> dict:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    raise AccessTokenError(f"no matching key for kid {kid!r}")


def verify_access_token(token: str, jwks: dict, *, audience: str, team_domain: str, now: int | None = None) -> AccessClaims:
    """Verify an Access JWT's signature and claims. Raises AccessTokenError."""
    header, payload, signing_input, signature = decode_jwt_unverified(token)
    if header.get("alg") != "RS256":
        raise AccessTokenError(f"unexpected alg {header.get('alg')!r}")

    jwk = find_jwk(jwks, header.get("kid", ""))
    if jwk.get("kty") != "RSA":
        raise AccessTokenError("signing key is not RSA")
    modulus_n = _jwk_int(jwk["n"])
    exponent_e = _jwk_int(jwk["e"])
    if not verify_rs256_signature(signing_input, signature, modulus_n, exponent_e):
        raise AccessTokenError("signature does not verify")

    now = int(time.time()) if now is None else now
    expires_at = payload.get("exp")
    if expires_at is not None and now >= expires_at:
        raise AccessTokenError("token has expired")

    audiences = payload.get("aud")
    if isinstance(audiences, str):
        audiences = [audiences]
    if audience not in (audiences or []):
        raise AccessTokenError("token audience does not match this application")

    expected_issuer = f"https://{team_domain}"
    if payload.get("iss") != expected_issuer:
        raise AccessTokenError("token issuer does not match the team domain")

    return AccessClaims(email=payload.get("email"), subject=payload.get("sub"), expires_at=expires_at)


async def fetch_jwks(team_domain: str) -> dict:
    """Fetch the team's JWKS. Only runs inside the Worker runtime."""
    from js import fetch  # type: ignore  # provided by the Pyodide/Workers runtime

    response = await fetch(f"https://{team_domain}/cdn-cgi/access/certs")
    text = await response.text()
    return json.loads(text)
