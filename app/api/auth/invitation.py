"""Reproduce invitation codes without storing the bearer code in plaintext."""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from app.core.config import settings


def _derive_code(user):
    message = f"vire:employee-invitation:{user.id}:{user.invite_code_seed}".encode()
    digest = hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:18]).decode().rstrip("=")


def stored_invitation_code(user):
    if not user.invite_code_seed:
        return None
    code = _derive_code(user)
    if not hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), user.invite_code_hash or ""):
        return None
    return code


def issue_invitation_code(user):
    user.invite_code_seed = secrets.token_hex(32)
    code = _derive_code(user)
    user.invite_code_hash = hashlib.sha256(code.encode()).hexdigest()
    user.invite_code_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    return code
