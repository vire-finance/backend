from collections.abc import Callable

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.api.auth.dependencies import get_current_user
from app.api.auth.model import User
from app.api.security.tokens import ActionType
from app.core.config import settings


def require_pin_verification(action_type: ActionType) -> Callable:
    def verify_action_token(
        action_token: str | None = Header(default=None, alias="X-Action-Token"),
        current_user: User = Depends(get_current_user),
    ) -> None:
        if action_token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="PIN verification is required.",
            )
        try:
            payload = jwt.decode(
                action_token,
                settings.SECRET_KEY,
                algorithms=["HS256"],
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="PIN verification has expired.",
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid PIN verification token.",
            )

        if payload.get("purpose") != "pin_verification":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid PIN verification token.",
            )
        if (
            payload.get("userId") != str(current_user.id)
            or payload.get("sub") != str(current_user.id)
            or payload.get("actionType") != action_type.value
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="PIN verification is not valid for this action.",
            )

    return verify_action_token
