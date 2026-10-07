from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import uuid4

import jwt

from app.core.config import settings

PIN_VERIFICATION_TTL = timedelta(minutes=5)


class ActionType(str, Enum):
    PAYMENT = "PAYMENT"
    APPROVAL = "APPROVAL"


def create_pin_verification_token(user_id: str, action_type: ActionType, session_id: str, pin_version: int) -> str:
    verified_at = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "userId": user_id,
            "actionType": action_type.value,
            "verifiedAt": int(verified_at.timestamp()),
            "purpose": "pin_verification",
            "sid": session_id,
            "pin_version": pin_version,
            "jti": str(uuid4()),
            "iat": verified_at,
            "exp": verified_at + PIN_VERIFICATION_TTL,
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
