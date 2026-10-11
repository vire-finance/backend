"""Ingress probes use a small ASGI app; no database, workers or inference calls."""
import asyncio
import json
from uuid import uuid4

from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient
import pytest

from app.api.auth.security import create_access_token, create_refresh_token
from app.core.request_guard import LocalRateLimiter, LocalRequestGuardMiddleware


def token(role="EMPLOYEE", user=None, *, refresh=False):
    creator = create_refresh_token if refresh else create_access_token
    return creator(user or uuid4(), role, uuid4())


def scope(path="/ocr/documents", *, method="POST", access=None, peer="127.0.0.1", headers=()):
    values = list(headers)
    if access is not None:
        values.append((b"authorization", ("Bearer " + access).encode()))
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "headers": values,
            "client": (peer, 1234), "server": ("localhost", 8000)}


async def invoke(guard, request_scope, chunks=(b"",)):
    messages = [{"type": "http.request", "body": data, "more_body": i < len(chunks) - 1}
                for i, data in enumerate(chunks)]
    consumed = []
    sent = []

    async def receive():
        if messages:
            value = messages.pop(0)
            consumed.append(value)
            return value
        return {"type": "http.disconnect"}

    async def send(value):
        sent.append(value)

    await guard(request_scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body, consumed


class Echo:
    def __init__(self):
        self.calls = 0
        self.bodies = []

    async def __call__(self, request_scope, receive, send):
        self.calls += 1
        body = bytearray()
        if request_scope["method"] in {"POST", "PUT", "PATCH"}:
            while True:
                message = await receive()
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
        self.bodies.append(bytes(body))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.parametrize("access,expected", [(None, 401), ("wrong-token", 401),
    (token("OWNER"), 403), (token(refresh=True), 401), (token("PENDING"), 403)])
def test_upload_rejects_missing_wrong_credentials_and_roles_before_reading(access, expected):
    app = Echo()
    guard = LocalRequestGuardMiddleware(app, upload_limit=16)
    status, _, _, consumed = asyncio.run(invoke(guard, scope(access=access,
        headers=[(b"content-length", b"1000000000")]), [b"unread payload"]))
    assert status == expected
    assert not consumed and app.calls == 0


def test_content_length_limit_rejects_before_parser_and_without_reading():
    app = Echo()
    guard = LocalRequestGuardMiddleware(app, upload_limit=16)
    result = asyncio.run(invoke(guard, scope(access=token(),
        headers=[(b"content-length", b"17")]), [b"unread payload"]))
    assert result[0] == 413 and not result[3] and app.calls == 0


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")], [(b"transfer-encoding", b"chunked")]])
def test_chunked_and_missing_or_understated_length_cannot_spool_oversized_body(headers):
    app = Echo()
    guard = LocalRequestGuardMiddleware(app, upload_limit=16)
    status, _, _, consumed = asyncio.run(invoke(guard, scope(access=token(), headers=headers),
                                                [b"a" * 10, b"b" * 7, b"not read"]))
    assert status == 413 and len(consumed) == 2
    assert app.calls == 0  # The application/multipart parser never sees any part.


def test_allowed_chunks_replay_exact_bytes_and_json_has_a_smaller_cap():
    app = Echo()
    guard = LocalRequestGuardMiddleware(app, upload_limit=32, json_limit=8)
    accepted = asyncio.run(invoke(guard, scope(access=token()), [b"abcdefgh", b"ijklmnop"]))
    assert accepted[0] == 200 and app.bodies == [b"abcdefghijklmnop"]
    rejected = asyncio.run(invoke(guard, scope("/auth/login"), [b"a" * 9]))
    assert rejected[0] == 413 and app.calls == 1


def test_valid_multipart_with_overhead_still_reaches_fastapi_upload_parser():
    app = FastAPI()
    app.add_middleware(LocalRequestGuardMiddleware, upload_limit=1024)

    @app.post("/ocr/documents")
    async def upload(file: UploadFile = File(...)):
        return {"length": len(await file.read()), "filename": file.filename}

    # This app has no background notification/OCR workers or database dependencies.
    with TestClient(app) as client:
        response = client.post("/ocr/documents", headers={"Authorization": "Bearer " + token()},
                               files={"file": ("receipt.png", b"x" * 100, "image/png")})
    assert response.status_code == 200 and response.json() == {"length": 100, "filename": "receipt.png"}


@pytest.mark.parametrize("lengths", [[b"-1"], [b"junk"], [b"1", b"2"]])
def test_invalid_length_cannot_reach_parser(lengths):
    app = Echo()
    guard = LocalRequestGuardMiddleware(app)
    response = asyncio.run(invoke(guard, scope("/auth/login",
        headers=[(b"content-length", value) for value in lengths])))
    assert response[0] == 400 and app.calls == 0


def test_login_limit_uses_peer_address_not_spoofable_forwarded_headers_and_recovers():
    now = [0.0]
    app = Echo()
    limiter = LocalRateLimiter(clock=lambda: now[0])
    guard = LocalRequestGuardMiddleware(app, limiter=limiter, login_rate=2)
    for number in range(2):
        response = asyncio.run(invoke(guard, scope("/auth/login",
            headers=[(b"x-forwarded-for", f"attacker-{number}".encode())])))
        assert response[0] == 200
    blocked = asyncio.run(invoke(guard, scope("/auth/login")))
    assert blocked[0] == 429 and blocked[1][b"retry-after"] == b"60"
    assert asyncio.run(invoke(guard, scope("/auth/login", peer="127.0.0.2")))[0] == 200
    now[0] = 60
    assert asyncio.run(invoke(guard, scope("/auth/login")))[0] == 200


def test_ai_force_legacy_and_new_session_share_user_throttle_across_ips():
    app = Echo()
    guard = LocalRequestGuardMiddleware(app, ai_rate=2)
    user = uuid4()
    request_scope = scope("/ai/owner/insights/generate", access=token("OWNER", user))
    request_scope["query_string"] = b"force=true"
    assert asyncio.run(invoke(guard, request_scope))[0] == 200
    legacy = scope("/ai/fund-requests/" + str(uuid4()) + "/analysis", method="GET",
                   access=token("OWNER", user), peer="127.0.0.2")
    assert asyncio.run(invoke(guard, legacy))[0] == 200
    legacy["client"] = ("127.0.0.3", 1234)
    assert asyncio.run(invoke(guard, legacy))[0] == 429
    other = scope("/ai/owner/insights/generate", access=token("OWNER"), peer="127.0.0.4")
    assert asyncio.run(invoke(guard, other))[0] == 200


def test_limiter_is_bounded_and_does_not_evict_active_entries_for_new_ips():
    now = [0.0]
    limiter = LocalRateLimiter(capacity=2, clock=lambda: now[0])
    assert limiter.check([("ip", "a"), ("user", "b")], 2) is None
    assert limiter.check([("ip", "new")], 2) == 60
    assert len(limiter.entries) == 2
    now[0] = 61
    assert limiter.check([("ip", "new")], 2) is None and len(limiter.entries) == 1


def test_upload_concurrency_is_bounded_and_slots_release_after_completion():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        echo = Echo()

        async def app(request_scope, receive, send):
            entered.set()
            await release.wait()
            await echo(request_scope, receive, send)

        guard = LocalRequestGuardMiddleware(app, concurrent_uploads=1)
        first = asyncio.create_task(invoke(guard, scope(access=token())))
        await entered.wait()
        busy = await invoke(guard, scope(access=token()))
        assert busy[0] == 429 and busy[1][b"retry-after"] == b"1"
        release.set()
        assert (await first)[0] == 200
        assert (await invoke(guard, scope(access=token())))[0] == 200

    asyncio.run(run())


def test_slow_body_read_times_out_and_releases_upload_slot():
    async def run():
        app = Echo()
        guard = LocalRequestGuardMiddleware(app, concurrent_uploads=1, body_timeout=0.01)
        sent = []

        async def slow_receive():
            await asyncio.sleep(10)
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(value):
            sent.append(value)

        await guard(scope(access=token()), slow_receive, send)
        assert sent[0]["status"] == 408 and app.calls == 0
        assert (await invoke(guard, scope(access=token())))[0] == 200

    asyncio.run(run())
