from app.shared.enums import UserRole
import io
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest
from sqlalchemy import select

from app.api.auth.model import User
from app.api.profile import photo
from app.api.profile.model import ProfilePhoto
from app.core.config import settings
from app.core.storage import StorageError, LocalReceiptStorage
from test_main_funding import ctx, settings_card
from test_request_categories import employee_context
from test_e2e_authorization import client, headers


def png():
    stream = io.BytesIO()
    with Image.new('RGB', (16, 16), 'blue') as image:
        image.save(stream, format='PNG')
    return stream.getvalue()


def test_private_persistent_photo_replace_and_tenant_access(client, ctx, settings_card, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'UPLOAD_DIR', str(tmp_path))
    employee = employee_context(ctx, ctx.raw_card(settings_card).pocket_id)
    auth = headers(ctx.db, employee.user)
    assert client.post('/api/v1/profile/photo', files={'file': ('photo.png', png(), 'image/png')}).status_code == 401
    first = client.post('/api/v1/profile/photo', headers=auth, files={'file': ('photo.png', png(), 'image/png')})
    assert first.status_code == 200
    url = first.json()['avatar_url']
    profile = client.get('/api/v1/profile', headers=auth)
    assert profile.json()['avatar_url'] == url
    result = client.get(url, headers=auth)
    assert result.status_code == 200 and result.headers['content-type'] == 'image/jpeg'
    assert result.headers['cache-control'] == 'private, no-store'
    assert result.content.startswith(b'\xff\xd8')
    assert client.get(url).status_code == 401
    assert client.get(url, headers=headers(ctx.db, ctx.user)).status_code == 200
    outsider = User(email='photo-outsider@example.com', username='Other', phone_number='0800000000', password_hash='test', role=UserRole.OWNER)
    ctx.db.add(outsider); ctx.db.commit()
    assert client.get(url, headers=headers(ctx.db, outsider)).status_code == 404
    old = ctx.db.scalar(select(ProfilePhoto).where(ProfilePhoto.user_id == employee.user.id))
    old_file = Path(old.storage_path)
    assert client.post('/api/v1/profile/photo', headers=auth, files={'file': ('photo.png', png(), 'image/png')}).status_code == 200
    assert not old_file.exists() and len(list(tmp_path.glob('*.jpg'))) == 1


@pytest.mark.parametrize('name,content,mime,status', [
    ('x.svg', b'<svg/>', 'image/svg+xml', 415),
    ('x.png', b'not an image', 'image/png', 415),
    ('x.png', b'x' * (photo.MAX_PHOTO_BYTES + 1), 'image/png', 413),
], ids=['unsupported', 'corrupt', 'too-large'])
def test_invalid_photo(client, ctx, name, content, mime, status):
    response = client.post('/api/v1/profile/photo', headers=headers(ctx.db, ctx.user), files={'file': (name, content, mime)})
    assert response.status_code == status


def test_storage_failure_does_not_update_profile(client, ctx, monkeypatch):
    def fail(*args):
        raise StorageError('unavailable', retryable=True)
    monkeypatch.setattr(photo, 'get_receipt_storage', lambda: SimpleNamespace(save=fail))
    response = client.post('/api/v1/profile/photo', headers=headers(ctx.db, ctx.user), files={'file': ('x.png', png(), 'image/png')})
    assert response.status_code == 503 and response.json()['code'] == 'STORAGE_UNAVAILABLE'
    assert ctx.user.avatar_url is None


def test_database_failure_cleanup_does_not_remove_old_photo(ctx, tmp_path, monkeypatch):
    storage = LocalReceiptStorage(tmp_path)
    monkeypatch.setattr(photo, 'get_receipt_storage', lambda: storage)
    from sqlalchemy.exc import SQLAlchemyError
    def fail():
        raise SQLAlchemyError('synthetic failure')
    monkeypatch.setattr(ctx.db, 'flush', fail)
    with pytest.raises(SQLAlchemyError):
        photo.save_photo(png(), ctx.user, ctx.db)
    assert list(tmp_path.iterdir()) == []


def test_validation_response_does_not_echo_sensitive_input(client):
    marker = 'private-test-value-do-not-echo'
    result = client.post('/auth/login', json={'email': 'bad', 'password': marker, 'unexpected': marker})
    assert result.status_code == 422 and result.json()['code'] == 'VALIDATION_ERROR'
    assert marker not in result.text
    assert all('input' not in item and 'ctx' not in item for item in result.json()['detail'])
