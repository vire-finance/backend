"""Verify Google's signed ID token before issuing an application session."""
import re
import secrets

from fastapi import HTTPException
from google.auth import exceptions as google_errors
from google.auth.transport.requests import Request
from google.oauth2 import id_token as google_id_token
import requests
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.auth.model import User
from app.api.auth.security import hash_password
from app.core.config import settings


def verify_google_token(token):
    audience = settings.GOOGLE_CLIENT_ID.strip()
    if not audience:
        raise HTTPException(503, 'Login Google belum dikonfigurasi. Isi GOOGLE_CLIENT_ID di backend.')
    try:
        with requests.Session() as session:
            session.trust_env = False
            transport = Request(session=session)
            def request_with_timeout(*args, **kwargs):
                kwargs['timeout'] = 10
                return transport(*args, **kwargs)
            claims = google_id_token.verify_oauth2_token(token, request_with_timeout, audience=audience)
    except google_errors.TransportError:
        raise HTTPException(503, 'Google belum dapat dihubungi. Coba lagi.') from None
    except (ValueError, google_errors.GoogleAuthError):
        raise HTTPException(401, 'Token Google tidak valid atau kedaluwarsa.') from None
    except requests.RequestException:
        raise HTTPException(503, 'Google belum dapat dihubungi. Coba lagi.') from None
    if (claims.get('aud') != audience or claims.get('iss') not in
        {'accounts.google.com', 'https://accounts.google.com'} or
        claims.get('email_verified') is not True or not isinstance(claims.get('email'), str) or
        not claims.get('sub') or len(str(claims['sub'])) > 255):
        raise HTTPException(401, 'Identitas Google belum terverifikasi.')
    return claims


def google_user(db, claims):
    subject = str(claims['sub'])
    email = claims['email'].strip().lower()
    user = db.scalar(select(User).where(User.google_subject == subject))
    if user:
        return user
    user = db.scalar(select(User).where(func.lower(User.email) == email).with_for_update())
    if user:
        authoritative = email.endswith('@gmail.com') or bool(claims.get('hd'))
        if user.google_subject is not None or not authoritative:
            raise HTTPException(409, 'Email sudah terdaftar. Login dengan password akun tersebut untuk menghubungkannya.')
        user.google_subject = subject
    else:
        username = re.sub(r'[^A-Za-z0-9_.-]', '_', email.split('@')[0])[:100]
        if len(username) < 3:
            username = 'user_' + subject[-12:]
        user = User(email=email, username=username, phone_number='',
            full_name=str(claims.get('name') or username)[:200], google_subject=subject,
            password_hash=hash_password(secrets.token_urlsafe(48)), role=None)
        db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Akun sedang dibuat atau dihubungkan. Coba login Google kembali.') from None
    return user
