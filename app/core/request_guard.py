"""Bounded ingress and process-local throttling before FastAPI parses bodies.

JWT checks here cheaply reject missing credentials and disallowed upload roles.
The existing dependencies still validate the account, session and revocation in
the database; this middleware never substitutes for that authorization.
"""
from collections import OrderedDict
import asyncio
import math
from threading import Lock, Semaphore
import time
from uuid import UUID

from fastapi import HTTPException
from starlette.responses import JSONResponse

from app.api.auth.security import decode_access_token
from app.core.config import settings


class LocalRateLimiter:
    """Bounded fixed-window counters; active entries are never evicted to bypass limits."""

    def __init__(self, *, capacity=4096, window_seconds=60, clock=time.monotonic):
        self.capacity = capacity
        self.window_seconds = window_seconds
        self.clock = clock
        self.entries = OrderedDict()
        self.lock = Lock()

    def check(self, keys, limit):
        """Atomically consume IP/user counters, returning a retry delay when full."""
        now = self.clock()
        with self.lock:
            # Entries are inserted in expiry order and are not moved on reads.
            while self.entries and next(iter(self.entries.values()))[0] <= now:
                self.entries.popitem(last=False)
            for key in keys:
                expiry, count = self.entries.get(key, (now + self.window_seconds, 0))
                if count >= limit:
                    return max(1, math.ceil(expiry - now))
            missing = sum(key not in self.entries for key in keys)
            if len(self.entries) + missing > self.capacity:
                earliest = next(iter(self.entries.values()))[0] if self.entries else now + self.window_seconds
                return max(1, math.ceil(earliest - now))
            for key in keys:
                expiry, count = self.entries.get(key, (now + self.window_seconds, 0))
                self.entries[key] = (expiry, count + 1)
        return None


class LocalRequestGuardMiddleware:
    """Reject oversized bodies before multipart spooling, including chunked input.

    Allowed bodies are buffered up to the configured cap and replayed to the
    parser. A content-length hint alone is not trusted. Two bounded uploads may
    be read concurrently by default; expensive OCR execution has its own guard.
    """

    AUTH_PATHS = frozenset({"/auth/login", "/auth/register", "/auth/google", "/auth/refresh"})

    def __init__(self, app, *, limiter=None, json_limit=None, upload_limit=None,
                 login_rate=None, upload_rate=None, ai_rate=None, concurrent_uploads=None,
                 body_timeout=None):
        self.app = app
        self.limiter = limiter if limiter is not None else LocalRateLimiter()
        self.json_limit = settings.HTTP_MAX_JSON_BODY_BYTES if json_limit is None else json_limit
        self.upload_limit = (settings.MAX_UPLOAD_SIZE_BYTES + settings.HTTP_MULTIPART_OVERHEAD_BYTES
                             if upload_limit is None else upload_limit)
        self.rates = {
            "auth": settings.RATE_LOGIN_REQUESTS_PER_MINUTE if login_rate is None else login_rate,
            "upload": settings.RATE_UPLOAD_REQUESTS_PER_MINUTE if upload_rate is None else upload_rate,
            "ai": settings.RATE_AI_REQUESTS_PER_MINUTE if ai_rate is None else ai_rate,
        }
        count = settings.HTTP_MAX_CONCURRENT_UPLOADS if concurrent_uploads is None else concurrent_uploads
        self.upload_slots = Semaphore(count)
        self.body_timeout = settings.HTTP_BODY_TIMEOUT_SECONDS if body_timeout is None else body_timeout

    @staticmethod
    def _claims(scope):
        values = [value for key, value in scope.get("headers", ()) if key.lower() == b"authorization"]
        if len(values) != 1 or len(values[0]) > 8192:
            raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
        parts = values[0].decode("latin-1").split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
        claims = decode_access_token(parts[1])
        if claims.get("typ") == "refresh" or claims.get("purpose") == "pin_verification":
            raise HTTPException(401, "Access token required.")
        try:
            UUID(claims["sid"])
            claims["sub"] = str(UUID(claims["sub"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise HTTPException(401, "Invalid or expired token.") from None
        return claims

    @staticmethod
    async def _error(scope, receive, send, status, detail, *, retry=None, headers=None):
        response_headers = dict(headers or {})
        if retry is not None:
            response_headers["Retry-After"] = str(retry)
        await JSONResponse({"detail": detail}, status_code=status, headers=response_headers)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "").rstrip("/") or "/"
        upload = method == "POST" and path in {"/ocr/documents", "/api/v1/profile/photo"}
        costly_ai = ((method == "POST" and path == "/ai/owner/insights/generate") or
                     (method == "GET" and path.startswith("/ai/fund-requests/") and path.endswith("/analysis")))
        lane = "upload" if upload else "ai" if costly_ai else "auth" if method == "POST" and path in self.AUTH_PATHS else None
        claims = None
        if upload or costly_ai:
            try:
                claims = self._claims(scope)
                role = "EMPLOYEE" if upload else "OWNER"
                allowed_roles = {"OWNER", "EMPLOYEE"} if path == "/api/v1/profile/photo" else {role}
                if claims.get("role") not in allowed_roles:
                    detail = ("Only employee accounts can access OCR document scanning." if upload
                              else "Hanya Owner yang diizinkan.")
                    raise HTTPException(403, detail)
            except HTTPException as exc:
                await self._error(scope, receive, send, exc.status_code, exc.detail, headers=exc.headers)
                return
        if lane:
            # Do not trust forwarded headers supplied by an arbitrary local caller.
            # A deployment proxy must configure trusted forwarding at the ASGI server.
            peer = scope.get("client")
            address = peer[0] if peer else "unknown"
            keys = [(lane, "ip", address)]
            if claims is not None:
                keys.append((lane, "user", claims["sub"]))
            retry = self.limiter.check(keys, self.rates[lane])
            if retry is not None:
                await self._error(scope, receive, send, 429, "Terlalu banyak permintaan. Coba lagi nanti.", retry=retry)
                return
        if method not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        limit = self.upload_limit if upload else self.json_limit
        lengths = [value for key, value in scope.get("headers", ()) if key.lower() == b"content-length"]
        try:
            if len(lengths) > 1:
                raise ValueError
            declared = int(lengths[0]) if lengths else None
            if declared is not None and declared < 0:
                raise ValueError
        except ValueError:
            await self._error(scope, receive, send, 400, "Invalid Content-Length.")
            return
        if declared is not None and declared > limit:
            await self._error(scope, receive, send, 413, "Request body terlalu besar.")
            return
        acquired = False
        if upload:
            acquired = self.upload_slots.acquire(blocking=False)
            if not acquired:
                await self._error(scope, receive, send, 429, "Upload sedang sibuk. Coba lagi nanti.", retry=1)
                return
        try:
            body = bytearray()
            deadline = time.monotonic() + self.body_timeout
            while True:
                try:
                    remaining = deadline - time.monotonic()
                    message = await asyncio.wait_for(receive(), timeout=max(0, remaining))
                except TimeoutError:
                    await self._error(scope, receive, send, 408, "Request body melewati batas waktu.")
                    return
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    continue
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > limit:
                    await self._error(scope, receive, send, 413, "Request body terlalu besar.")
                    return
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            replayed = False

            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay, send)
        finally:
            if acquired:
                self.upload_slots.release()
