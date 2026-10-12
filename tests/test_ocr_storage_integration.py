"""PostgreSQL OCR/file-route integration with mocked private Storage only."""
import asyncio
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.api.auth.model import User
from app.api.ocr import service, worker
from app.api.ocr.model import OCRDocument
from app.api.ocr.files import with_storage_file_routes
from app.api.request.model import FundRequest
from app.api.request.router import router as original_request_router
from app.core import storage
from app.core.config import settings
from app.core.context import Context, get_context
from app.core.database import get_db
from app.main import app
from app.shared.enums import FundRequestStatus, OCRStatus, UserRole
from app.shared.utils import now_utc
from test_main_funding import ctx, settings_card
from test_request_categories import employee_context
from test_ocr_recovery import ocr_db, add_document
from test_receipt_storage import CONTENT, KEY, FakeDB


@pytest.fixture
def remote_storage(monkeypatch):
    objects, calls = {}, []
    def handle(request):
        calls.append((request.method, request.url.path))
        assert request.headers["apikey"] == KEY
        if request.url.path == "/storage/v1/bucket/receipts":
            return httpx.Response(200, json={"id": "receipts", "public": False})
        if request.method == "POST":
            key = request.url.path.split("/object/receipts/", 1)[1]
            assert CONTENT in request.content
            objects[key] = CONTENT
            return httpx.Response(200, json={"Key": "receipts/" + key})
        if request.method == "DELETE":
            import json
            for key in json.loads(request.content)["prefixes"]:
                objects.pop(key, None)
            return httpx.Response(200, json=[])
        key = request.url.path.split("/object/authenticated/receipts/", 1)[1]
        return httpx.Response(200, content=objects[key]) if key in objects else httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    backend = storage.SupabaseReceiptStorage("https://project.example", KEY, client=client)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(storage, "get_receipt_storage", lambda: backend)
    monkeypatch.setattr(service, "get_receipt_storage", lambda: backend)
    yield backend, objects, calls
    client.close()


def upload():
    return UploadFile(file=BytesIO(CONTENT), filename="receipt.png", headers=Headers({"content-type": "image/png"}))


def test_production_upload_recovery_and_ocr_survive_fresh_storage_client(ocr_db, remote_storage, monkeypatch, tmp_path):
    factory, user = ocr_db
    backend, objects, calls = remote_storage
    with factory() as db:
        document = asyncio.run(service.OCRService(db).upload_document(upload(), user))
        identifier, reference = document.id, document.storage_path
        assert document.ocr_status == OCRStatus.PENDING
        assert reference == f"supabase://receipts/{user}/{identifier}.png"
    assert list(Path(settings.UPLOAD_DIR).iterdir()) == []
    # Recreate the adapter as a restarted process would, keeping only DB/object data.
    restarted = storage.SupabaseReceiptStorage("https://project.example", KEY, client=backend.client)
    monkeypatch.setattr(storage, "get_receipt_storage", lambda: restarted)
    assert identifier in worker.pending_documents()
    temporary_paths = []
    def extraction(file_path, **kwargs):
        temporary_paths.append(Path(file_path))
        assert Path(file_path).read_bytes() == CONTENT
        return "Synthetic Shop\nTOTAL Rp 150.000"
    monkeypatch.setattr(service, "run_ocr", extraction)
    service.process_ocr_document_task(identifier)
    with factory() as db:
        result = db.get(OCRDocument, identifier)
        assert result.ocr_status == OCRStatus.COMPLETED and int(result.extracted_total_amount) == 150000
        assert result.storage_path == reference
    assert objects and all(not path.parent.exists() for path in temporary_paths)


def test_production_requeues_only_stale_processing_jobs(ocr_db, remote_storage):
    old = add_document(ocr_db, OCRStatus.PROCESSING,
                       now_utc() - timedelta(seconds=settings.OCR_STALE_JOB_SECONDS + 1))
    fresh = add_document(ocr_db, OCRStatus.PROCESSING, now_utc())
    terminal = add_document(ocr_db, OCRStatus.FAILED)
    assert old in worker.pending_documents()
    with ocr_db[0]() as db:
        assert db.get(OCRDocument, old).ocr_status == OCRStatus.PENDING
        assert db.get(OCRDocument, fresh).ocr_status == OCRStatus.PROCESSING
        assert db.get(OCRDocument, terminal).ocr_status == OCRStatus.FAILED


@pytest.mark.parametrize("retryable,expected", [(True, OCRStatus.PENDING), (False, OCRStatus.FAILED)])
def test_download_failures_keep_object_and_appropriate_job_status(ocr_db, remote_storage, monkeypatch, retryable, expected):
    backend, objects, _ = remote_storage
    with ocr_db[0]() as db:
        document = asyncio.run(service.OCRService(db).upload_document(upload(), ocr_db[1]))
        identifier = document.id
    from contextlib import contextmanager
    @contextmanager
    def fail(*args):
        raise storage.StorageError("unavailable" if retryable else "missing", retryable=retryable)
        yield
    monkeypatch.setattr(backend, "materialize", fail)
    monkeypatch.setattr(service, "run_ocr", lambda **kwargs: pytest.fail("No partial file may reach OCR"))
    service.process_ocr_document_task(identifier)
    with ocr_db[0]() as db:
        assert db.get(OCRDocument, identifier).ocr_status == expected
    assert len(objects) == 1


def test_remote_object_is_compensated_on_definite_database_failure(remote_storage):
    _, objects, calls = remote_storage
    with pytest.raises(RuntimeError, match="SYNTHETIC_DB_FAILURE"):
        asyncio.run(service.OCRService(FakeDB("flush")).upload_document(upload(), uuid4()))
    assert objects == {} and any(method == "DELETE" for method, _ in calls)


@pytest.fixture
def attached_receipt(ctx, settings_card, remote_storage):
    employee = employee_context(ctx, ctx.raw_card(settings_card).pocket_id)
    document = asyncio.run(service.OCRService(ctx.db).upload_document(upload(), employee.user.id))
    row = FundRequest(pocket_id=ctx.raw_card(settings_card).pocket_id,
        requester_id=employee.user.id, explanation="Synthetic request", party_name="Synthetic Shop",
        total_amount=100, needed_by=now_utc() + timedelta(days=1), status=FundRequestStatus.APPROVED)
    ctx.db.add(row)
    ctx.db.flush()
    document.fund_request_id = row.id
    row.receipt_document_id = document.id
    ctx.db.commit()
    yield employee, row, document
    app.dependency_overrides.clear()


@pytest.mark.parametrize("suffix,disposition", [
    ("documents/{doc}/file", "inline"), ("documents/{doc}/file?download=true", "attachment"),
    ("receipt/file", "inline"),
])
def test_private_preview_download_and_receipt_keep_contract(ctx, attached_receipt, remote_storage, suffix, disposition):
    employee, row, doc = attached_receipt
    app.dependency_overrides[get_context] = lambda: ctx
    with TestClient(app) as client:
        result = client.get(f"/fund-requests/{row.id}/" + suffix.format(doc=doc.id))
    assert result.status_code == 200 and result.content == CONTENT
    assert result.headers["content-type"] == "image/png"
    assert result.headers["content-disposition"].startswith(disposition)
    assert result.headers["cache-control"] == "private, no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert "location" not in result.headers and KEY not in str(result.headers)


def test_private_range_response(ctx, attached_receipt, remote_storage):
    _, row, doc = attached_receipt
    app.dependency_overrides[get_context] = lambda: ctx
    with TestClient(app) as client:
        result = client.get(f"/fund-requests/{row.id}/documents/{doc.id}/file", headers={"Range": "bytes=0-7"})
    assert result.status_code == 206 and result.content == CONTENT[:8]
    assert result.headers["content-range"] == f"bytes 0-7/{len(CONTENT)}"


@pytest.mark.parametrize("scenario,status", [("owner_draft", 403), ("same_business_employee", 403),
    ("other_business", 404), ("unattached_document", 404), ("unauthenticated", 401)])
def test_existing_visibility_checks_deny_before_storage_read(ctx, attached_receipt, remote_storage, scenario, status):
    employee, row, doc = attached_receipt
    if scenario == "owner_draft":
        row.status = FundRequestStatus.DRAFT
        selected = ctx
    elif scenario in {"same_business_employee", "other_business"}:
        user = User(email=f"{scenario}@example.test", username=scenario, phone_number="0800000002",
            password_hash="synthetic", role=UserRole.EMPLOYEE if scenario == "same_business_employee" else UserRole.OWNER,
            employer_id=ctx.user.id if scenario == "same_business_employee" else None)
        ctx.db.add(user)
        ctx.db.flush()
        selected = Context(ctx.db, user)
    else:
        selected = employee
        if scenario == "unattached_document":
            doc.fund_request_id = None
    if scenario != "unauthenticated":
        app.dependency_overrides[get_context] = lambda: selected
    app.dependency_overrides[get_db] = lambda: ctx.db
    before = sum("/object/authenticated/" in path for _, path in remote_storage[2])
    with TestClient(app) as client:
        result = client.get(f"/fund-requests/{row.id}/documents/{doc.id}/file")
    assert result.status_code == status
    assert sum("/object/authenticated/" in path for _, path in remote_storage[2]) == before


def test_route_composition_does_not_duplicate_or_change_other_endpoints():
    old = {(route.path, frozenset(route.methods)): route.endpoint for route in original_request_router.routes}
    active = with_storage_file_routes(original_request_router).routes
    assert len(active) == len(old)
    file_paths = {"/fund-requests/{request_id}/receipt/file", "/fund-requests/{request_id}/documents/{document_id}/file"}
    for route in active:
        if route.path not in file_paths:
            assert route.endpoint is old[(route.path, frozenset(route.methods))]
    # New FastAPI versions can register nested routers lazily; OpenAPI is the
    # public flattened view, while endpoint identity is checked on composition.
    schema = app.openapi()
    for path, methods in old:
        assert all(method.lower() in schema["paths"][path] for method in methods)


@pytest.mark.parametrize("code,retryable,status", [("missing", False, 404), ("unavailable", True, 503)])
def test_file_storage_failure_returns_safe_json_before_headers(ctx, attached_receipt, remote_storage, monkeypatch, code, retryable, status):
    from contextlib import contextmanager
    _, row, doc = attached_receipt
    @contextmanager
    def fail(*args):
        raise storage.StorageError(code, retryable=retryable)
        yield
    monkeypatch.setattr(remote_storage[0], "materialize", fail)
    app.dependency_overrides[get_context] = lambda: ctx
    with TestClient(app) as client:
        result = client.get(f"/fund-requests/{row.id}/documents/{doc.id}/file")
    assert result.status_code == status and set(result.json()) == {"detail", "code"}
    assert result.json()["code"] == ("FILE_NOT_FOUND" if status == 404 else "STORAGE_UNAVAILABLE")
    assert KEY not in result.text and "supabase://" not in result.text
    if retryable:
        assert result.headers["retry-after"] == "5"
