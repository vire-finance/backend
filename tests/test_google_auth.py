import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from google.auth import crypt, jwt
from sqlalchemy import func, select

from tests.test_main_funding import ctx
from app.api.auth import google
from app.api.auth.model import User
from app.api.auth.router import google_login
from app.api.auth.schema import GoogleLoginRequest
from app.api.auth.security import decode_access_token
from app.core.config import settings

CLIENT = 'test-client.apps.googleusercontent.com'

@pytest.fixture
def signed_token(monkeypatch):
    monkeypatch.setattr(settings, 'GOOGLE_CLIENT_ID', CLIENT)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test-google')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256()))
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    signer = crypt.RSASigner.from_string(private, key_id='local-test-key')
    response = SimpleNamespace(status=200, data=json.dumps({'local-test-key':
        cert.public_bytes(serialization.Encoding.PEM).decode()}).encode())
    monkeypatch.setattr(google, 'Request', lambda session: lambda *args, **kwargs: response)
    def make(**overrides):
        claims = {'iss': 'https://accounts.google.com', 'aud': CLIENT, 'sub': 'google-subject-123',
          'email': 'employee@gmail.com', 'email_verified': True, 'name': 'Employee',
          'iat': int(time.time()) - 5, 'exp': int(time.time()) + 600}
        claims.update(overrides)
        return jwt.encode(signer, claims).decode()
    return make


def test_real_signed_token_verifies(signed_token):
    assert google.verify_google_token(signed_token())['sub'] == 'google-subject-123'


@pytest.mark.parametrize('overrides', [
    {'aud': 'someone-elses-client'}, {'exp': int(time.time()) - 600},
    {'iss': 'https://attacker.example'}, {'email_verified': False},
])
def test_reject_wrong_audience_expired_issuer_and_unverified_email(signed_token, overrides):
    with pytest.raises(HTTPException) as error:
        google.verify_google_token(signed_token(**overrides))
    assert error.value.status_code == 401


def test_reject_modified_signature(signed_token):
    token = signed_token()
    header, payload, signature = token.split('.')
    changed = ('A' if signature[0] != 'A' else 'B') + signature[1:]
    with pytest.raises(HTTPException) as error:
        google.verify_google_token('.'.join((header, payload, changed)))
    assert error.value.status_code == 401


def test_google_registration_issues_pending_session_and_reuses_identity(ctx, signed_token):
    payload = GoogleLoginRequest(id_token=signed_token())
    result = google_login(payload, ctx.db)
    claims = decode_access_token(result.access_token)
    assert result.role == claims['role'] == 'PENDING'
    user = ctx.db.scalar(select(User).where(User.google_subject == 'google-subject-123'))
    assert user.role is None and user.employer_id is None
    again = google_login(payload, ctx.db)
    assert decode_access_token(again.access_token)['sub'] == str(user.id)
    assert ctx.db.scalar(select(func.count(User.id)).where(User.google_subject == 'google-subject-123')) == 1


def test_verified_gmail_link_preserves_owner_password_and_role(ctx, signed_token):
    ctx.user.email = 'employee@gmail.com'
    previous_password = ctx.user.password_hash
    ctx.db.commit()
    result = google_login(GoogleLoginRequest(id_token=signed_token()), ctx.db)
    assert result.role == 'OWNER'
    assert ctx.user.google_subject == 'google-subject-123'
    assert ctx.user.password_hash == previous_password


def test_external_email_collision_cannot_take_over_existing_account(ctx, signed_token):
    ctx.user.email = 'employee@example.test'
    ctx.db.commit()
    with pytest.raises(HTTPException) as error:
        google_login(GoogleLoginRequest(id_token=signed_token(email='employee@example.test')), ctx.db)
    assert error.value.status_code == 409
    assert ctx.user.google_subject is None


def test_google_disabled_until_client_id_configured(monkeypatch):
    monkeypatch.setattr(settings, 'GOOGLE_CLIENT_ID', '')
    with pytest.raises(HTTPException) as error:
        google.verify_google_token('x' * 30)
    assert error.value.status_code == 503
