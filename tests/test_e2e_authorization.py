from app.shared.enums import UserRole
"""Direct HTTP security checks: no Flutter or authentication bypass."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.auth.router import _issue_token
from app.api.auth.security import decode_access_token, hash_password
from app.api.funding.service import FundingService
from app.api.request.service import FundRequestService
from app.api.security.tokens import ActionType, create_pin_verification_token
from app.api.transaction.model import Transaction
from app.core.database import get_db
from app.main import app
from test_main_funding import ctx, settings_card
from test_purchase_workflow import purchase


def headers(db, user, action=ActionType.APPROVAL):
    token = _issue_token(db, user)
    sid = decode_access_token(token.access_token)['sid']
    return {'Authorization': 'Bearer ' + token.access_token,
            'X-Action-Token': create_pin_verification_token(str(user.id), action, sid, user.security_pin_version),
            'Idempotency-Key': str(uuid4())}


@pytest.fixture
def client(ctx):
    app.dependency_overrides[get_db] = lambda: ctx.db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_employee_cannot_authorize_and_owner_can(client, ctx, settings_card):
    employee, row = purchase(ctx, settings_card)
    auth = headers(ctx.db, employee.user)
    count = ctx.db.scalar(select(func.count(Transaction.id)))
    for suffix, payload in [('approve', {'card_id': str(settings_card)}),
                            ('reject', {'reason': 'Not allowed'}),
                            ('receipt/review', {'decision': 'VERIFIED'})]:
        assert client.post(f'/fund-requests/{row.id}/{suffix}', headers=auth, json=payload).status_code == 403
    direct = client.post('/transactions', headers=headers(ctx.db, employee.user, ActionType.PAYMENT),
        json={'card_id': str(settings_card), 'request_id': str(row.id), 'amount': 50,
              'description': row.explanation, 'category': row.category,
              'payment_method': row.payment_method, 'recipient_account': row.recipient_account})
    assert direct.status_code == 403
    assert row.status.value == 'PENDING_APPROVAL'
    assert ctx.db.scalar(select(func.count(Transaction.id))) == count
    assert FundingService(ctx).get()['balance'] == 1000
    assert client.post(f'/fund-requests/{row.id}/approve', json={'card_id': str(settings_card)}).status_code == 401
    good = headers(ctx.db, ctx.user)
    response = client.post(f'/fund-requests/{row.id}/approve', headers=good, json={'card_id': str(settings_card)})
    assert response.status_code == 200
    assert client.post(f'/fund-requests/{row.id}/approve', headers=good, json={'card_id': str(settings_card)}).json()['id'] == response.json()['id']
    assert FundingService(ctx).get()['balance'] == 950


def test_direct_payment_cannot_bypass_self_approval(client, ctx, settings_card):
    _, row = purchase(ctx, settings_card)
    # Legacy/corrupt self-request must fail before a durable attempt is created.
    row.requester_id = ctx.user.id
    ctx.db.commit()
    response = client.post('/transactions', headers=headers(ctx.db, ctx.user, ActionType.PAYMENT),
        json={'card_id': str(settings_card), 'request_id': str(row.id), 'amount': 50,
              'description': row.explanation, 'category': row.category,
              'payment_method': row.payment_method, 'recipient_account': row.recipient_account})
    assert response.status_code == 403
    from fastapi import HTTPException
    from app.api.transaction.service import TransactionService
    with pytest.raises(HTTPException) as error:
        TransactionService(ctx).execute(settings_card, 50, row.explanation, uuid4(),
            request_id=row.id, category=row.category, payment_method=row.payment_method,
            recipient_account=row.recipient_account)
    assert error.value.status_code == 403
    assert ctx.db.scalar(select(func.count(Transaction.id))) == 0
    assert FundingService(ctx).get()['balance'] == 1000


def test_employee_role_login_rotation_restore_and_old_session_denial(client, ctx, settings_card):
    employee, _ = purchase(ctx, settings_card)
    employee.user.email = 'role-check@example.com'
    employee.user.password_hash = hash_password('TestPassword123!')
    ctx.db.commit()
    for _ in range(2):
        login = client.post('/auth/login', json={'email': employee.user.email, 'password': 'TestPassword123!'})
        assert login.status_code == 200
        tokens = login.json()
        assert tokens['role'] == decode_access_token(tokens['access_token'])['role'] == 'EMPLOYEE'
        for _ in range(2):
            old = tokens
            refreshed = client.post('/auth/refresh', headers={'Authorization': 'Bearer ' + old['refresh_token']})
            assert refreshed.status_code == 200
            tokens = refreshed.json()
            claims = decode_access_token(tokens['access_token'])
            assert tokens['role'] == claims['role'] == 'EMPLOYEE'
            assert claims['sub'] == str(employee.user.id)
            assert client.get('/api/v1/profile', headers={'Authorization': 'Bearer ' + old['access_token']}).status_code == 401
            auth = {'Authorization': 'Bearer ' + tokens['access_token']}
            assert client.get('/api/v1/profile', headers=auth).json()['role'] == 'EMPLOYEE'
            assert client.get('/api/v1/workspace', headers=auth).status_code == 403
        assert client.post('/auth/logout', headers=auth).status_code == 204
        assert client.get('/api/v1/profile', headers=auth).status_code == 401


def test_other_company_cannot_review_or_view_request(client, ctx, settings_card):
    from app.api.auth.model import User
    _, row = purchase(ctx, settings_card)
    outsider = User(email='outsider-review@example.com', username='Other', phone_number='0800000000', password_hash='test', role=UserRole.OWNER)
    ctx.db.add(outsider); ctx.db.commit()
    auth = headers(ctx.db, outsider)
    for suffix, payload in [('approve', {'card_id': str(settings_card)}), ('reject', {}),
                            ('clarification', {'message': 'Not authorized'})]:
        assert client.post(f'/fund-requests/{row.id}/{suffix}', headers=auth, json=payload).status_code == 404
    assert client.get(f'/fund-requests/{row.id}', headers=auth).status_code == 404
    assert row.status.value == 'PENDING_APPROVAL'
    assert FundingService(ctx).get()['balance'] == 1000
