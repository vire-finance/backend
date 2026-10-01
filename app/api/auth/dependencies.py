import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.auth.model import AuthSession, User
from app.api.auth.security import decode_access_token
from app.core.database import get_db

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    claims = decode_access_token(credentials.credentials)
    if claims.get("typ") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access token required")
    try:
        user_id, session_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    session = db.get(AuthSession, session_id)
    user = db.get(User, user_id)
    now = datetime.now(timezone.utc)
    if (not session or session.user_id != user_id or session.revoked_at is not None
            or session.expires_at <= now or not user
            or (user.role.value if user.role else "PENDING") != claims.get("role")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return user


def get_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Pilih role terlebih dahulu.")
    return user
