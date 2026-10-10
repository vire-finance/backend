import hashlib
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.api.auth.dependencies import get_current_user
from app.api.auth.invitation import issue_invitation_code, stored_invitation_code
from app.api.auth.model import User
from app.api.auth.router import select_owner_role, select_employee_role
from app.api.auth.schema import OwnerSetupRequest, EmployeeSetupRequest
from app.core.database import get_db
from app.main import app
from app.shared.enums import UserRole
from test_main_funding import ctx


def test_setup_code_is_retrievable_and_employee_can_join(ctx):
    ctx.user.role = None
    ctx.db.commit()
    result = select_owner_role(OwnerSetupRequest(fund_account_type='BANK', employees_managed=3), ctx.user, ctx.db)
    assert result.code == stored_invitation_code(ctx.user)
    assert ctx.user.invite_code_seed != result.code
    employee = User(email='invite@example.com', username='invite', phone_number='0800000011', password_hash='test', role=None)
    ctx.db.add(employee); ctx.db.commit()
    select_employee_role(EmployeeSetupRequest(invitation_code=result.code), employee, ctx.db)
    assert employee.employer_id == ctx.user.id
    assert stored_invitation_code(ctx.user) == result.code


def test_invitation_http_read_is_stable_and_regenerate_replaces_code(ctx):
    first = issue_invitation_code(ctx.user)
    ctx.db.commit()
    expiry = ctx.user.invite_code_expires_at
    app.dependency_overrides[get_current_user] = lambda: ctx.user
    app.dependency_overrides[get_db] = lambda: ctx.db
    try:
        with TestClient(app) as client:
            for _ in range(2):
                data = client.get('/auth/invite-code').json()
                assert data['code'] == first
                assert data['expired'] is False
            assert ctx.user.invite_code_expires_at == expiry
            response = client.post('/auth/invite-code/regenerate')
            assert response.status_code == 200
            replacement = response.json()['code']
            assert replacement != first
            assert client.get('/auth/invite-code').json()['code'] == replacement
            assert ctx.user.invite_code_hash == hashlib.sha256(replacement.encode()).hexdigest()
    finally:
        app.dependency_overrides.clear()


def test_employee_cannot_read_or_regenerate_invitation(ctx):
    employee = User(email='denied@example.com', username='denied', phone_number='0800000012', password_hash='test', role=UserRole.EMPLOYEE, employer_id=ctx.user.id)
    ctx.db.add(employee); ctx.db.commit()
    app.dependency_overrides[get_current_user] = lambda: employee
    app.dependency_overrides[get_db] = lambda: ctx.db
    try:
        with TestClient(app) as client:
            assert client.get('/auth/invite-code').status_code == 403
            assert client.post('/auth/invite-code/regenerate').status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_legacy_hash_is_preserved_and_expired_code_cannot_join(ctx):
    from app.api.auth.router import get_invite_code
    ctx.user.invite_code_hash = hashlib.sha256(b'legacy-code-123456').hexdigest()
    ctx.user.invite_code_expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    ctx.db.commit()
    original_hash = ctx.user.invite_code_hash
    assert get_invite_code(ctx.user)['code'] is None
    assert ctx.user.invite_code_hash == original_hash
    code = issue_invitation_code(ctx.user)
    ctx.user.invite_code_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    ctx.db.commit()
    assert get_invite_code(ctx.user)['expired'] is True
    employee = User(email='expired@example.com', username='expired', phone_number='0800000013', password_hash='test', role=None)
    ctx.db.add(employee); ctx.db.commit()
    with pytest.raises(HTTPException) as error:
        select_employee_role(EmployeeSetupRequest(invitation_code=code), employee, ctx.db)
    assert error.value.status_code == 400
