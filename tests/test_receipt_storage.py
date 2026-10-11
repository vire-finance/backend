"""Storage HTTP and compensation tests use synthetic data and mocked Supabase."""
import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import HTTPException, UploadFile
from pydantic import SecretStr, ValidationError
from starlette.datastructures import Headers

from app.api.ocr import service
from app.api.ocr.files import ReceiptFileResponse
from app.core import storage
from app.core.config import Settings, settings
from app.core.storage import LocalReceiptStorage, StorageError, SupabaseReceiptStorage

KEY = "sb_secret_SYNTHETIC_BACKEND_KEY"
CONTENT = b"\x89PNG\r\n\x1a\nsynthetic-receipt"


def identity():
    user, document = uuid4(), uuid4()
    filename = f"{document}.png"
    return user, document, filename, f"supabase://receipts/{user}/{filename}"


def cloud(handler, timeout=15):
    return SupabaseReceiptStorage("https://project.example", KEY, timeout=timeout,
        client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_cloud_upload_download_delete_protocol_and_cleanup():
    user, document, filename, reference = identity()
    calls = []
    def handle(request):
        calls.append(request)
        assert request.headers["apikey"] == KEY
        assert "authorization" not in request.headers
        assert "/public/" not in request.url.path and "/sign/" not in request.url.path
        if request.method == "POST":
            assert request.url.path == f"/storage/v1/object/receipts/{user}/{filename}"
            assert request.headers["x-upsert"] == "false"
            assert CONTENT in request.content and b"image/png" in request.content
            return httpx.Response(200, json={"Key": "receipts/" + filename})
        if request.method == "GET":
            assert request.url.path == f"/storage/v1/object/authenticated/receipts/{user}/{filename}"
            return httpx.Response(200, content=CONTENT)
        assert request.method == "DELETE" and request.url.path == "/storage/v1/object/receipts"
        import json
        assert json.loads(request.content) == {"prefixes": [f"{user}/{filename}"]}
        return httpx.Response(200, json=[])
    backend = cloud(handle)
    assert backend.save(CONTENT, user, filename, "image/png") == reference
    doc = SimpleNamespace(id=document, user_id=user, stored_filename=filename,
                          storage_path=reference, file_size=len(CONTENT))
    with backend.materialize(reference, len(CONTENT)) as path:
        assert path.read_bytes() == CONTENT
        parent = path.parent
    assert not parent.exists()
    backend.delete(reference)
    assert len(calls) == 3


def test_legacy_service_role_authentication_and_reject_anon_key():
    key = jwt.encode({"role": "service_role"}, "synthetic-signing-key")
    backend = SupabaseReceiptStorage("https://project.example", key)
    assert backend.headers == {"apikey": key, "Authorization": "Bearer " + key}
    for invalid in ("sb_publishable_SYNTHETIC", jwt.encode({"role": "anon"}, "synthetic-signing-key")):
        with pytest.raises(StorageError) as error:
            SupabaseReceiptStorage("https://project.example", invalid)
        assert invalid not in str(error.value)


@pytest.mark.parametrize("public", [True, None, "false"])
def test_startup_rejects_non_private_bucket(public):
    backend = cloud(lambda request: httpx.Response(200, json={"id": "receipts", "public": public}))
    with pytest.raises(StorageError) as error:
        backend.validate_bucket()
    assert error.value.code == "bucket_not_private"


def test_startup_accepts_existing_private_bucket():
    cloud(lambda request: httpx.Response(200, json={"id": "receipts", "public": False})).validate_bucket()


@pytest.mark.parametrize("status,body,code,retryable", [
    (404, {}, "missing", False), (400, {"statusCode": "404"}, "missing", False),
    (400, {"code": "NoSuchKey"}, "missing", False),
    (401, {}, "unavailable", False), (403, {}, "unavailable", False),
    (429, {}, "unavailable", True), (500, {}, "unavailable", True),
    (302, {}, "unavailable", False),
])
def test_storage_http_failures_are_sanitized(status, body, code, retryable):
    _, _, _, reference = identity()
    backend = cloud(lambda request: httpx.Response(status, json={**body, "message": KEY + " PRIVATE_RECEIPT"}))
    with pytest.raises(StorageError) as error:
        with backend.materialize(reference, len(CONTENT)):
            pytest.fail("A failed download must not expose a file")
    assert error.value.code == code and error.value.retryable is retryable
    assert KEY not in str(error.value) and "PRIVATE_RECEIPT" not in str(error.value)


@pytest.mark.parametrize("content", [CONTENT[:-1], CONTENT + b"oversized"])
def test_download_rejects_incomplete_or_oversized_object_and_cleans_temp(monkeypatch, tmp_path, content):
    monkeypatch.setattr(storage.tempfile, "tempdir", str(tmp_path))
    _, _, _, reference = identity()
    backend = cloud(lambda request: httpx.Response(200, content=content))
    with pytest.raises(StorageError) as error:
        with backend.materialize(reference, len(CONTENT)):
            pytest.fail("Incorrect size must not reach OCR or the user")
    assert error.value.code == "invalid_size" and not error.value.retryable
    assert list(tmp_path.iterdir()) == []


def test_download_total_deadline_and_temp_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(storage.tempfile, "tempdir", str(tmp_path))
    clock = iter([100, 116])
    monkeypatch.setattr(storage.time, "monotonic", lambda: next(clock))
    _, _, _, reference = identity()
    backend = cloud(lambda request: httpx.Response(200, content=CONTENT), timeout=15)
    with pytest.raises(StorageError) as error:
        with backend.materialize(reference, len(CONTENT)):
            pytest.fail("A download beyond its total deadline must fail")
    assert error.value.code == "download_timeout" and error.value.retryable
    assert list(tmp_path.iterdir()) == []


def test_timeout_and_local_temp_failure_are_sanitized(monkeypatch):
    _, _, _, reference = identity()
    def timeout(request):
        raise httpx.ReadTimeout(KEY + " PRIVATE_RECEIPT", request=request)
    with pytest.raises(StorageError) as error:
        with cloud(timeout).materialize(reference, len(CONTENT)):
            pass
    assert error.value.retryable and KEY not in str(error.value)
    def broken_temp(**kwargs):
        raise OSError(KEY)
    monkeypatch.setattr(storage.tempfile, "TemporaryDirectory", broken_temp)
    with pytest.raises(StorageError):
        with cloud(timeout).materialize(reference, len(CONTENT)):
            pass


@pytest.mark.parametrize("reference", ["supabase://public/other/file.png", "supabase://receipts/../file.png",
    "supabase://receipts/user/../../secret", "https://attacker.example/receipt.png"])
def test_cloud_references_cannot_select_other_buckets_or_hosts(reference):
    backend = cloud(lambda request: pytest.fail("Invalid reference must not make HTTP requests"))
    with pytest.raises(StorageError):
        with backend.materialize(reference, 100):
            pass


def test_reference_must_belong_to_the_document(monkeypatch):
    user, document, filename, reference = identity()
    doc = SimpleNamespace(id=document, user_id=uuid4(), stored_filename=filename,
                          storage_path=reference, file_size=len(CONTENT))
    with pytest.raises(StorageError) as error:
        with storage.materialize_document(doc):
            pass
    assert error.value.code == "invalid_reference"


def test_local_storage_roundtrip_traversal_and_deletion(tmp_path):
    user, _, filename, _ = identity()
    backend = LocalReceiptStorage(tmp_path / "uploads")
    reference = backend.save(CONTENT, user, filename, "image/png")
    with backend.materialize(reference, len(CONTENT)) as path:
        assert path.read_bytes() == CONTENT
    assert path.exists()
    with pytest.raises(StorageError):
        with backend.materialize(str(tmp_path / "outside.png"), 10):
            pass
    backend.delete(reference)
    backend.delete(reference)
    assert not path.exists()


def test_environment_selection_preserves_development_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    assert isinstance(storage.get_receipt_storage(), LocalReceiptStorage)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://project.example")
    monkeypatch.setattr(settings, "SUPABASE_SECRET_KEY", SecretStr(KEY))
    assert isinstance(storage.get_receipt_storage(), SupabaseReceiptStorage)


def production_settings(**overrides):
    return Settings(_env_file=None, APP_ENV="production", SECRET_KEY="synthetic-vire-key",
        PROD_DATABASE_URL="postgresql+psycopg://synthetic", PROD_LLM_API_KEY="synthetic-groq-key",
        **{"SUPABASE_URL": "https://project.example", "SUPABASE_SECRET_KEY": KEY, **overrides})


@pytest.mark.parametrize("overrides", [{"SUPABASE_URL": ""}, {"SUPABASE_URL": "http://project.example"},
    {"SUPABASE_URL": "https://user:password@project.example"},
    {"SUPABASE_URL": "https://project.example?secret=value"}, {"SUPABASE_SECRET_KEY": ""}])
def test_production_requires_safe_storage_configuration(overrides):
    with pytest.raises(ValidationError) as error:
        production_settings(**overrides)
    assert KEY not in str(error.value) and "password@" not in str(error.value)


def test_configuration_keeps_secrets_hidden_and_environment_switching():
    configured = production_settings()
    assert KEY not in repr(configured) and KEY not in configured.model_dump_json()
    assert configured.DATABASE_URL == configured.PROD_DATABASE_URL
    assert configured.LLM_PROVIDER == configured.PROD_LLM_PROVIDER
    with pytest.raises(ValidationError, match="OCR and Storage timeouts"):
        production_settings(OCR_JOB_TIMEOUT_SECONDS=90, OCR_STALE_JOB_SECONDS=100, STORAGE_TIMEOUT_SECONDS=15)


class FakeDB:
    def __init__(self, fail):
        self.fail, self.document = fail, None
        self.committed = False
        self.commit_finishing = False
    def add(self, document):
        self.document = document
    def flush(self):
        if self.fail == "flush":
            raise RuntimeError("SYNTHETIC_DB_FAILURE")
    def commit(self):
        if self.fail == "commit":
            raise RuntimeError("SYNTHETIC_DB_FAILURE")
        if self.fail == "commit_in_flight":
            self.commit_finishing = True
            raise RuntimeError("SYNTHETIC_DB_FAILURE")
        self.committed = True
        if self.fail == "commit_ack":
            raise RuntimeError("SYNTHETIC_DB_FAILURE")
    def refresh(self, document):
        if self.fail == "refresh":
            raise RuntimeError("SYNTHETIC_DB_FAILURE")
    def rollback(self):
        pass


@pytest.mark.parametrize("failure,keep", [
    ("flush", False), ("commit", True), ("commit_ack", True),
    ("commit_in_flight", True), ("refresh", True),
])
def test_upload_database_failures_preserve_committed_or_uncertain_receipts(monkeypatch, tmp_path, failure, keep):
    backend = LocalReceiptStorage(tmp_path)
    monkeypatch.setattr(service, "get_receipt_storage", lambda: backend)
    db = FakeDB(failure)
    upload = UploadFile(file=BytesIO(CONTENT), filename="receipt.png", headers=Headers({"content-type": "image/png"}))
    with pytest.raises(RuntimeError, match="SYNTHETIC_DB_FAILURE"):
        asyncio.run(service.OCRService(db).upload_document(upload, uuid4()))
    assert bool(list(tmp_path.iterdir())) is keep


def test_cleanup_failure_keeps_original_database_failure(monkeypatch, tmp_path, caplog):
    backend = LocalReceiptStorage(tmp_path)
    def broken_delete(reference):
        raise RuntimeError(KEY)
    monkeypatch.setattr(backend, "delete", broken_delete)
    monkeypatch.setattr(service, "get_receipt_storage", lambda: backend)
    upload = UploadFile(file=BytesIO(CONTENT), filename="receipt.png", headers=Headers({"content-type": "image/png"}))
    with pytest.raises(RuntimeError, match="SYNTHETIC_DB_FAILURE"):
        asyncio.run(service.OCRService(FakeDB("flush")).upload_document(upload, uuid4()))
    assert "cleanup deferred" in caplog.text and KEY not in caplog.text


def test_remote_upload_failure_never_creates_a_database_row(monkeypatch, caplog):
    backend = cloud(lambda request: httpx.Response(500, json={"message": KEY + " PRIVATE_RECEIPT"}))
    monkeypatch.setattr(service, "get_receipt_storage", lambda: backend)
    db = FakeDB(None)
    upload = UploadFile(file=BytesIO(CONTENT), filename="receipt.png", headers=Headers({"content-type": "image/png"}))
    with pytest.raises(StorageError) as error:
        asyncio.run(service.OCRService(db).upload_document(upload, uuid4()))
    assert db.document is None and error.value.uncertain
    assert KEY not in caplog.text and "PRIVATE_RECEIPT" not in caplog.text


@pytest.mark.parametrize("filename,mime,content,expected", [
    ("receipt.txt", "image/png", CONTENT, 400),
    ("receipt.png", "application/pdf", CONTENT, 400),
    ("receipt.png", "image/png", b"invalid image", 400),
    ("receipt.png", "image/png", CONTENT * 100, 413),
])
def test_invalid_uploads_are_rejected_before_storage(monkeypatch, filename, mime, content, expected):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 100)
    monkeypatch.setattr(service, "get_receipt_storage", lambda: pytest.fail("Invalid files must not access storage"))
    upload = UploadFile(file=BytesIO(content), filename=filename, headers=Headers({"content-type": mime}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.OCRService(FakeDB(None)).upload_document(upload, uuid4()))
    assert error.value.status_code == expected


def test_asgi_send_failure_cleans_downloaded_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(storage.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(settings, "APP_ENV", "production")
    backend = cloud(lambda request: httpx.Response(200, content=CONTENT))
    monkeypatch.setattr(storage, "get_receipt_storage", lambda: backend)
    user, identifier, filename, reference = identity()
    document = SimpleNamespace(id=identifier, user_id=user, stored_filename=filename, storage_path=reference,
        file_size=len(CONTENT), mime_type="image/png", original_filename="receipt.png")
    async def send(message):
        raise OSError("synthetic disconnected client")
    async def receive():
        return {"type": "http.disconnect"}
    async def run():
        await ReceiptFileResponse(document)({"type": "http", "method": "GET", "headers": []}, receive, send)
    with pytest.raises(OSError):
        asyncio.run(run())
    assert list(tmp_path.iterdir()) == []
