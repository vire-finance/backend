from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from app.api.auth.dependencies import get_current_user
from app.api.auth.model import User, AuthSession
from app.api.auth.security import hash_password, verify_password, create_access_token
from app.api.security.router import change_password
from app.api.security.schema import ChangePasswordRequest
from app.core.database import get_db
from app.main import app
from app.shared.enums import UserRole
from test_main_funding import ctx

OLD = 'OldPassword123'
NEW = 'NewPassword456'

@pytest.fixture
def password_user(ctx):
    ctx.user.password_hash = hash_password(OLD)
    ctx.db.commit()
    return ctx.user


def test_password_change_revokes_all_user_sessions_and_preserves_other_accounts(ctx, password_user):
    other = User(email='other-password@example.com', username='other', phone_number='0800000060', password_hash=hash_password(OLD), role=UserRole.OWNER)
    ctx.db.add(other); ctx.db.flush()
    sessions = [AuthSession(user_id=password_user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=1)) for _ in range(2)]
    other_session = AuthSession(user_id=other.id, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    ctx.db.add_all(sessions + [other_session]); ctx.db.commit()
    token = create_access_token(str(password_user.id), password_user.role.value, str(sessions[0].id))
    change_password(ChangePasswordRequest(current_password=OLD, new_password=NEW, confirm_password=NEW), password_user, ctx.db)
    assert verify_password(NEW, password_user.password_hash)
    assert not verify_password(OLD, password_user.password_hash)
    for session in sessions + [other_session]:
        ctx.db.refresh(session)
    assert all(session.revoked_at is not None for session in sessions)
    assert other_session.revoked_at is None
    assert verify_password(OLD, other.password_hash)
    with pytest.raises(HTTPException) as error:
        get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=token), ctx.db)
    assert error.value.status_code == 401


def test_wrong_current_password_does_not_change_password_or_sessions(ctx, password_user):
    session = AuthSession(user_id=password_user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    ctx.db.add(session); ctx.db.commit()
    original = password_user.password_hash
    with pytest.raises(HTTPException) as error:
        change_password(ChangePasswordRequest(current_password='WrongPassword1', new_password=NEW, confirm_password=NEW), password_user, ctx.db)
    assert error.value.status_code == 400
    assert password_user.password_hash == original
    assert session.revoked_at is None


def test_password_change_http_validation_and_requires_auth(ctx, password_user):
    with TestClient(app) as client:
        assert client.post('/api/v1/security/change-password', json={'current_password': OLD, 'new_password': NEW, 'confirm_password': NEW}).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: password_user
    app.dependency_overrides[get_db] = lambda: ctx.db
    try:
        with TestClient(app) as client:
            for password, confirmation in [('short1A', 'short1A'), ('lowercase123', 'lowercase123'), (NEW, 'Mismatch123'), (OLD, OLD)]:
                response = client.post('/api/v1/security/change-password', json={'current_password': OLD, 'new_password': password, 'confirm_password': confirmation})
                assert response.status_code == 422
                assert verify_password(OLD, password_user.password_hash)
            response = client.post('/api/v1/security/change-password', json={'current_password': OLD, 'new_password': NEW, 'confirm_password': NEW})
            assert response.status_code == 200
            assert verify_password(NEW, password_user.password_hash)
    finally:
        app.dependency_overrides.clear()


def test_employee_can_change_own_password(ctx, password_user):
    password_user.role = UserRole.EMPLOYEE
    ctx.db.commit()
    change_password(ChangePasswordRequest(current_password=OLD, new_password=NEW, confirm_password=NEW), password_user, ctx.db)
    assert verify_password(NEW, password_user.password_hash)
