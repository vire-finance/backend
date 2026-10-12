from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from app.api.auth.model import AuthSession
from app.api.auth.router import _issue_token
from app.api.auth.security import decode_access_token
from app.api.notification.model import Notification, PushDevice, PushDelivery
from app.api.notification import worker
from app.core.config import settings
from test_main_funding import ctx, settings_card
from test_request_categories import employee_context
from test_e2e_authorization import client, headers


@pytest.fixture
def fcm(monkeypatch):
    monkeypatch.setattr(settings, 'FIREBASE_SERVICE_ACCOUNT', 'synthetic.json')
    monkeypatch.setattr(settings, 'FIREBASE_PROJECT_ID', 'synthetic-project')
    monkeypatch.setattr(worker, '_credentials', SimpleNamespace(valid=True, token='synthetic-secret'))
    calls = []
    def send(*args, **kwargs):
        calls.append(kwargs['json']['message'])
        return SimpleNamespace(ok=True, status_code=200, json=lambda: {})
    monkeypatch.setattr('requests.post', send)
    return calls


def device(ctx, suffix):
    tokens = _issue_token(ctx.db, ctx.user)
    row = PushDevice(user_id=ctx.user.id, session_id=UUID(decode_access_token(tokens.access_token)['sid']), token='synthetic-device-token-' + suffix)
    ctx.db.add(row); ctx.db.commit()
    return row


def notification(ctx):
    row = Notification(user_id=ctx.user.id, title='Private merchant', message='Private financial amount')
    ctx.db.add(row); ctx.db.commit()
    return row


def test_partial_delivery_retry_skips_successful_device(ctx, fcm, monkeypatch):
    first, second = device(ctx, 'first'), device(ctx, 'second')
    event = notification(ctx)
    def send(*args, **kwargs):
        message = kwargs['json']['message']; fcm.append(message)
        return SimpleNamespace(ok=message['token'] == first.token, status_code=503, json=lambda: {})
    monkeypatch.setattr('requests.post', send)
    worker.dispatch(ctx.db)
    assert not event.push_processed
    assert ctx.db.get(PushDelivery, (event.id, first.id)) is not None
    event.push_retry_at = datetime.utcnow() - timedelta(seconds=1); ctx.db.commit()
    monkeypatch.setattr('requests.post', lambda *a, **kw: (fcm.append(kw['json']['message']) or SimpleNamespace(ok=True)))
    worker.dispatch(ctx.db)
    assert event.push_processed
    assert sum(message['token'] == first.token for message in fcm) == 1
    assert sum(message['token'] == second.token for message in fcm) == 2
    assert all(message['notification']['title'] == 'VIRE' for message in fcm)
    assert all(message['android']['notification']['tag'] == str(event.id) for message in fcm)
    assert 'Private' not in str(fcm)


def test_invalid_token_deleted_missing_configuration_logged(ctx, fcm, monkeypatch, caplog):
    row = device(ctx, 'invalid'); identifier = row.id
    event = notification(ctx)
    monkeypatch.setattr('requests.post', lambda *a, **kw: SimpleNamespace(ok=False,
        json=lambda: {'error': {'details': [{'errorCode': 'UNREGISTERED'}]}}))
    worker.dispatch(ctx.db)
    assert ctx.db.get(PushDevice, identifier) is None and event.push_processed
    monkeypatch.setattr(settings, 'FIREBASE_SERVICE_ACCOUNT', '')
    monkeypatch.setattr(worker, '_missing_configuration_logged', False)
    worker.dispatch(ctx.db)
    assert 'delivery disabled' in caplog.text
    assert 'synthetic-secret' not in caplog.text


def test_device_rotation_switching_logout_and_legacy_binding(client, ctx, settings_card, fcm):
    employee = employee_context(ctx, ctx.raw_card(settings_card).pocket_id)
    token = 'synthetic-device-token-shared'
    owner_auth = headers(ctx.db, ctx.user)
    assert client.post('/notifications/devices', headers=owner_auth, json={'token': token}).status_code == 200
    old = ctx.db.scalar(select(PushDevice).where(PushDevice.token == token))
    old_session = old.session_id
    from app.api.auth.security import create_refresh_token
    refresh = create_refresh_token(ctx.user.id, 'OWNER', old_session)
    new = client.post('/auth/refresh', headers={'Authorization': 'Bearer ' + refresh})
    assert new.status_code == 200
    ctx.db.refresh(old)
    assert old.session_id != old_session
    old_event = notification(ctx)
    employee_auth = headers(ctx.db, employee.user)
    assert client.post('/notifications/devices', headers=employee_auth, json={'token': token}).status_code == 200
    worker.dispatch(ctx.db)
    assert fcm == []  # Owner's pending event cannot reach reassigned device.
    event = Notification(user_id=employee.user.id, title='Private', message='Private')
    ctx.db.add(event); ctx.db.commit()
    assert client.post('/auth/logout', headers=employee_auth).status_code == 204
    worker.dispatch(ctx.db)
    assert fcm == []
    ctx.db.add(PushDevice(user_id=ctx.user.id, token='synthetic-legacy-device-token'))
    ctx.db.commit()
    worker.dispatch(ctx.db)
    assert fcm == []
