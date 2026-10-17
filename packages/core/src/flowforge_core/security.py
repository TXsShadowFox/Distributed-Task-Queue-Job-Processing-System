"""Credential primitives: Argon2id passwords, JWT access tokens, opaque refresh tokens and API keys."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from flowforge_core.config import Settings
from flowforge_core.errors import AuthenticationFailed, ValidationFailed

# ------------------------------------------------------------------------------------------------ passwords
_hasher = PasswordHasher()  # Argon2id, RFC 9106 parameters as packaged by argon2-cffi
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
# Verifying against this hash for unknown users keeps login timing uniform (no user enumeration via timing).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def validate_password_policy(password: str, *, email: str | None = None) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailed(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationFailed(f"password must be at most {MAX_PASSWORD_LENGTH} characters")
    if len(set(password)) < 4:
        raise ValidationFailed("password is too repetitive")
    if email and password.lower() == email.lower():
        raise ValidationFailed("password must not equal the email address")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# ------------------------------------------------------------------------------------------------ JWT
@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    token_id: str
    issued_at: datetime
    expires_at: datetime
    password_version: int


def password_version(password_changed_at: datetime) -> int:
    """Embedded in access tokens; changing the password changes it, which invalidates all earlier tokens.
    Compared for equality (not ordering), so clock skew between hosts cannot cause false rejections."""
    return int(password_changed_at.timestamp() * 1000)


def create_access_token(
    user_id: uuid.UUID,
    settings: Settings,
    *,
    password_changed_at: datetime,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(seconds=settings.access_token_ttl_seconds)
    claims = {
        "sub": str(user_id),
        "pwv": password_version(password_changed_at),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_hex(12),
        "typ": "access",
    }
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
    return token, expires


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],  # never accept "none" or asymmetric-confusion algorithms
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            leeway=10,
            options={"require": ["exp", "iat", "sub", "jti", "iss", "aud", "pwv"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationFailed("access token expired") from None
    except jwt.PyJWTError:
        raise AuthenticationFailed("invalid access token") from None
    if claims.get("typ") != "access" or not isinstance(claims.get("pwv"), int):
        raise AuthenticationFailed("invalid access token")
    try:
        user_id = uuid.UUID(claims["sub"])
    except ValueError:
        raise AuthenticationFailed("invalid access token") from None
    return AccessTokenClaims(
        user_id=user_id,
        token_id=str(claims["jti"]),
        issued_at=datetime.fromtimestamp(claims["iat"], UTC),
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        password_version=claims["pwv"],
    )


# ------------------------------------------------------------------------------------------------ opaque tokens
def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """Returns (raw token for the client, hash for storage)."""
    raw = secrets.token_urlsafe(48)
    return raw, sha256_hex(raw)


# ------------------------------------------------------------------------------------------------ API keys
API_KEY_PREFIX = "ffk"
_API_KEY_RE = re.compile(r"^ffk_([0-9a-f]{12})_([A-Za-z0-9_-]{43})$")


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    raw: str  # shown to the user exactly once
    prefix: str  # public identifier, safe to display and log
    key_hash: str


def generate_api_key() -> GeneratedApiKey:
    public_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    raw = f"{API_KEY_PREFIX}_{public_id}_{secret}"
    return GeneratedApiKey(
        raw=raw, prefix=f"{API_KEY_PREFIX}_{public_id}", key_hash=sha256_hex(raw)
    )


def looks_like_api_key(value: str) -> bool:
    return bool(_API_KEY_RE.fullmatch(value))


def api_key_hash(raw: str) -> str:
    """API keys carry 256 bits of entropy, so a fast hash is sufficient (no offline guessing is feasible)."""
    return sha256_hex(raw)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
