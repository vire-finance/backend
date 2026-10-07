from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from app.api.auth.dependencies import bearer_scheme
from app.api.auth.security import decode_access_token
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from app.api.auth.dependencies import get_current_user
from app.api.auth.model import User
from app.api.security.pin import hash_pin, verify_pin
from app.api.security.schema import (
    BiometricToggleRequest,
    ChangePinRequest,
    NotificationPreferencesRequest,
    VerifyPinRequest,
    VerifyPinResponse,
)
from app.api.security.tokens import (
    ActionType,
    PIN_VERIFICATION_TTL,
    create_pin_verification_token,
)
from app.core.database import get_db

router = APIRouter(prefix="/api/v1/security", tags=["Security"])
MAX_PIN_ATTEMPTS = 3
PIN_LOCK_DURATION_MINUTES = 15


def _get_user_with_pin(db: Session, user_id) -> User:
    user = db.scalar(
        select(User)
        .options(undefer(User.security_pin))
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return user


def _check_pin(db: Session, user: User, supplied_pin: str | None, old_pin_check: bool = False) -> None:
    now = datetime.now(timezone.utc)
    locked_until = user.pin_locked_until
    if locked_until is not None:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until > now:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "message": "PIN verification is temporarily locked.",
                    "remaining_attempts": 0,
                    "retry_after_seconds": int((locked_until - now).total_seconds()),
                },
            )
        user.pin_locked_until = None
        user.pin_failed_attempts = 0

    valid_pin = (
        user.security_pin is not None
        and verify_pin(supplied_pin or "", user.security_pin)
    )
    if not valid_pin:
        user.pin_failed_attempts += 1
        remaining_attempts = max(
            0, MAX_PIN_ATTEMPTS - user.pin_failed_attempts
        )
        if remaining_attempts == 0:
            user.pin_locked_until = now + timedelta(
                minutes=PIN_LOCK_DURATION_MINUTES
            )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The current PIN is incorrect." if old_pin_check else {
                "message": "PIN verification failed.",
                "remaining_attempts": remaining_attempts,
            },
        )


@router.post("/change-pin")
def change_pin(
    payload: ChangePinRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user = _get_user_with_pin(db, current_user.id)
    if user.security_pin is not None:
        _check_pin(db, user, payload.old_pin, old_pin_check=True)
    elif payload.old_pin is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No current PIN is set.",
        )

    user.security_pin = hash_pin(payload.new_pin)
    user.security_pin_version += 1
    user.pin_failed_attempts = 0
    user.pin_locked_until = None
    db.commit()
    return {"message": "Security PIN updated."}


@router.patch("/biometric-toggle")
def toggle_biometric(
    payload: BiometricToggleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    current_user.biometric_enabled = payload.enabled
    db.commit()
    return {"biometric_enabled": current_user.biometric_enabled}


@router.patch("/notifications")
def update_notifications(
    payload: NotificationPreferencesRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, dict[str, bool]]:
    preferences = dict(current_user.notification_preferences or {})
    preferences.update(payload.model_dump(exclude_unset=True, exclude_none=True))
    current_user.notification_preferences = preferences
    db.commit()
    return {"notification_preferences": preferences}


@router.post("/verify-pin", response_model=VerifyPinResponse)
def verify_security_pin(
    payload: VerifyPinRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VerifyPinResponse:
    user = _get_user_with_pin(db, current_user.id)
    _check_pin(db, user, payload.pin)

    user.pin_failed_attempts = 0
    user.pin_locked_until = None
    db.commit()

    action_token = create_pin_verification_token(
        str(user.id), ActionType(payload.action_type),
        decode_access_token(credentials.credentials)["sid"], user.security_pin_version
    )
    return VerifyPinResponse(
        action_token=action_token,
        expires_in=int(PIN_VERIFICATION_TTL.total_seconds()),
    )


@router.get("/settings")
def get_security_settings(current_user: User = Depends(get_current_user)):
    return {
        "pin_is_set": current_user.security_pin is not None,
        "biometric_enabled": current_user.biometric_enabled,
        "notification_preferences": current_user.notification_preferences,
    }
