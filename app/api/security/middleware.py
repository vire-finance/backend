from collections.abc import Callable

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.api.auth.dependencies import bearer_scheme, get_current_user
from fastapi.security import HTTPAuthorizationCredentials
from app.api.auth.security import decode_access_token
from app.api.auth.model import User
from app.api.security.tokens import ActionType
from app.core.config import settings
from app.core.database import get_db
from app.shared.enums import UserRole
from sqlalchemy import select
from sqlalchemy.orm import Session


def require_pin_verification(action_type: ActionType, owner_only: bool = False) -> Callable:
    def verify_action_token(
        action_token: str | None = Header(default=None, alias="X-Action-Token"),
        current_user: User = Depends(get_current_user),
        credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
        db: Session = Depends(get_db),
    ) -> None:
        if owner_only and current_user.role != UserRole.OWNER:
            raise HTTPException(status_code=403, detail="Only owners can perform this action.")
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
                options={"require": ["exp", "iat", "sub", "sid", "jti", "pin_version"]},
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
            or payload.get("sid") != decode_access_token(credentials.credentials).get("sid")
            or payload.get("pin_version") != db.scalar(select(User.security_pin_version).where(User.id == current_user.id))
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="PIN verification is not valid for this action.",
            )

    return verify_action_token
