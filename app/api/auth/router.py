import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth.dependencies import bearer_scheme, get_current_user
from app.api.auth.model import AuthSession, User
from app.api.auth.schema import (
    EmployeeSetupRequest, InviteResponse, LoginRequest, OwnerSetupRequest,
    RegisterRequest, RegistrationResponse, TokenResponse, UserResponse,
)
from app.api.auth.security import (
    ACCESS_TOKEN_MINUTES, SESSION_DAYS, create_access_token, create_refresh_token,
    decode_access_token, hash_password, verify_password,
)
from app.core.database import get_db
from app.shared.enums import UserRole

router = APIRouter(prefix="/auth", tags=["Authentication"])
GENERIC_LOGIN_ERROR = "Email atau password tidak valid."


@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
):
    email = str(payload.email).strip().lower()

    # Check duplicate email
    if db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email sudah terdaftar.",
        )

    # Create user
    user = User(
        email=email,
        username=payload.username.strip(),
        phone_number=payload.phone_number.strip(),
        password_hash=hash_password(payload.password.get_secret_value()),
        role=None,
    )

    try:
        db.add(user)
        db.flush()

        # Create authentication session
        session = AuthSession(
            user_id=user.id,
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=SESSION_DAYS),
        )

        db.add(session)
        db.flush()

        role = user.role.value if user.role else "PENDING"

        access_token = create_access_token(
            user.id,
            role,
            session.id,
        )

        refresh_token = create_refresh_token(
            user.id,
            role,
            session.id,
        )

        db.commit()

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email sudah terdaftar.",
        )

    except Exception:
        db.rollback()
        raise

    return RegistrationResponse(
        user=UserResponse(
            id=str(user.id),
            email=user.email,
            username=user.username,
            phone_number=user.phone_number,
            role=role if role != "PENDING" else None,
        ),
        setup_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_MINUTES * 60,
    )

@router.post("/role/owner", response_model=InviteResponse)
def select_owner_role(payload: OwnerSetupRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Role sudah dipilih.")
    user.role = UserRole.OWNER
    user.fund_account_type = payload.fund_account_type.strip().upper()
    user.employees_managed = payload.employees_managed
    code = secrets.token_urlsafe(18)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    user.invite_code_hash = hashlib.sha256(code.encode()).hexdigest()
    user.invite_code_expires_at = expires_at
    db.commit()
    return InviteResponse(code=code, expires_at=expires_at.isoformat())


@router.post("/role/employee", response_model=UserResponse)
def select_employee_role(payload: EmployeeSetupRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Role sudah dipilih.")
    code_hash = hashlib.sha256(payload.invitation_code.encode()).hexdigest()
    owner = db.scalar(select(User).where(User.invite_code_hash == code_hash))
    now = datetime.now(timezone.utc)
    if not owner or owner.role != UserRole.OWNER or not owner.invite_code_expires_at or owner.invite_code_expires_at <= now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation code tidak valid atau kedaluwarsa.")
    count = db.scalar(select(func.count(User.id)).where(User.employer_id == owner.id)) or 0
    if owner.employees_managed is not None and count >= owner.employees_managed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Kapasitas employee owner sudah penuh.")
    user.role = UserRole.EMPLOYEE
    user.employer_id = owner.id
    db.commit()
    db.refresh(user)
    db.commit()

    return UserResponse(
        id=str(user.id),
        email=user.email,
        username=user.username,
        phone_number=user.phone_number,
        role=user.role.value if user.role else None,
    )

def _issue_token(db: Session, user: User) -> TokenResponse:
    session = AuthSession(user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS))
    db.add(session)
    db.commit()
    db.refresh(session)
    role = user.role.value if user.role else "PENDING"
    return TokenResponse(
        access_token=create_access_token(user.id, role, session.id),
        refresh_token=create_refresh_token(user.id, role, session.id),
        expires_in=ACCESS_TOKEN_MINUTES * 60,
        role=role,
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if not user or not verify_password(payload.password.get_secret_value(), user.password_hash) or not user.role:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)
    return _issue_token(db, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required")
    claims = decode_access_token(credentials.credentials)
    if claims.get("typ") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required")
    session = db.get(AuthSession, uuid.UUID(claims["sid"]))
    user = db.get(User, uuid.UUID(claims["sub"]))
    now = datetime.now(timezone.utc)
    if not session or not user or session.user_id != user.id or session.revoked_at or session.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")
    role = user.role.value if user.role else "PENDING"
    return TokenResponse(access_token=create_access_token(user.id, role, session.id), refresh_token=create_refresh_token(user.id, role, session.id), expires_in=ACCESS_TOKEN_MINUTES * 60, role=role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    from app.api.auth.security import decode_access_token
    claims = decode_access_token(credentials.credentials)
    session = db.get(AuthSession, uuid.UUID(claims["sid"]))
    if session and session.user_id == user.id:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invite-code/regenerate", response_model=InviteResponse)
def regenerate_invite_code(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != UserRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hanya owner yang dapat membuat invitation code.")
    code = secrets.token_urlsafe(18)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    user.invite_code_hash = hashlib.sha256(code.encode()).hexdigest()
    user.invite_code_expires_at = expires_at
    db.commit()
    return InviteResponse(code=code, expires_at=expires_at.isoformat())
