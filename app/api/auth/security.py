import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from app.core.config import settings

PASSWORD_ITERATIONS = 600_000
ACCESS_TOKEN_MINUTES = 30
SESSION_DAYS = 30


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _b64decode(salt), int(rounds))
        return hmac.compare_digest(actual, _b64decode(expected))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: uuid.UUID, role: str, session_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user_id), "role": role, "sid": str(session_id),
        "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=ACCESS_TOKEN_MINUTES)).timestamp()),
    }
    signing_input = f"{_json_b64(header)}.{_json_b64(payload)}"
    signature = hmac.new(settings.SECRET_KEY.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64encode(signature)}"


def create_refresh_token(user_id: uuid.UUID, role: str, session_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": str(user_id), "role": role, "sid": str(session_id), "typ": "refresh",
               "iat": int(now.timestamp()), "exp": int((now + timedelta(days=SESSION_DAYS)).timestamp())}
    signing_input = f"{_json_b64(header)}.{_json_b64(payload)}"
    signature = hmac.new(settings.SECRET_KEY.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64encode(signature)}"


def decode_access_token(token: str) -> dict:
    try:
        header, payload, signature = token.split(".")
        signing_input = f"{header}.{payload}"
        expected = hmac.new(settings.SECRET_KEY.encode(), signing_input.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(signature)):
            raise ValueError("invalid signature")
        data = json.loads(_b64decode(payload))
        decoded_header = json.loads(_b64decode(header))
        if not isinstance(data, dict) or not isinstance(decoded_header, dict):
            raise ValueError("invalid token structure")
        if decoded_header.get("alg") != "HS256":
            raise ValueError("invalid algorithm")
        if not isinstance(data.get("exp"), (int, float)) or data["exp"] <= datetime.now(timezone.utc).timestamp():
            raise ValueError("expired")
        return data
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _json_b64(value: dict) -> str:
    return _b64encode(json.dumps(value, separators=(",", ":")).encode())
