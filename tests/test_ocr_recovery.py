import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.core.models
from app.api.auth.model import User
from app.api.ocr import service, worker, tesseract
from app.api.ocr.model import OCRDocument
from app.core.config import settings
from app.core.database import engine
from app.shared.base import Base
from app.shared.enums import OCRStatus, UserRole
from app.shared.utils import now_utc


@pytest.fixture
def ocr_db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    schema = "test_ocr_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = create_engine(make_url(settings.DATABASE_URL).update_query_dict(
        {"options": "-csearch_path=" + schema}))
    factory = sessionmaker(bind=isolated, expire_on_commit=False)
    try:
        Base.metadata.create_all(isolated)
        with factory() as db:
            user = User(email="ocr@example.test", username="ocr", phone_number="0800000000",
                        password_hash="test", role=UserRole.EMPLOYEE)
            db.add(user)
            db.commit()
            user_id = user.id
        monkeypatch.setattr(service, "SessionLocal", factory)
        yield factory, user_id
    finally:
        isolated.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def add_document(ocr_db, status=OCRStatus.PENDING, updated_at=None):
    factory, user_id = ocr_db
    name = uuid.uuid4().hex + ".png"
    from pathlib import Path
    path = Path(settings.UPLOAD_DIR) / name
    path.write_bytes(b"synthetic OCR fixture")
    with factory() as db:
        doc = OCRDocument(user_id=user_id, original_filename=name, stored_filename=name,
                          storage_path=str(path), mime_type="image/png", file_size=path.stat().st_size, ocr_status=status)
        if updated_at is not None:
            doc.updated_at = updated_at
        db.add(doc)
        db.commit()
        return doc.id


def test_duplicate_background_and_recovery_claim_only_once(ocr_db, monkeypatch):
    document_id = add_document(ocr_db)
    calls = []
    def extraction(**kwargs):
        calls.append(kwargs)
        time.sleep(0.1)
        return "TOKO VIRE\nTOTAL Rp 150.000"
    monkeypatch.setattr(service, "run_ocr", extraction)
    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(service.process_ocr_document_task, [document_id] * 5))
    assert len(calls) == 1
    with ocr_db[0]() as db:
        doc = db.get(OCRDocument, document_id)
        assert doc.ocr_status == OCRStatus.COMPLETED
        assert int(doc.extracted_total_amount) == 150000


def test_processing_jobs_are_bounded(ocr_db, monkeypatch):
    identifiers = [add_document(ocr_db) for _ in range(5)]
    active = maximum = 0
    lock = threading.Lock()
    def extraction(**kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.1)
        with lock:
            active -= 1
        return "TOTAL Rp 10.000"
    monkeypatch.setattr(service, "run_ocr", extraction)
    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(service.process_ocr_document_task, identifiers))
    assert maximum <= settings.OCR_MAX_CONCURRENT_JOBS
    with ocr_db[0]() as db:
        assert all(db.get(OCRDocument, identifier).ocr_status == OCRStatus.COMPLETED
                   for identifier in identifiers)


def test_restart_recovers_pending_and_fails_only_stale_processing(ocr_db):
    pending = add_document(ocr_db)
    old = add_document(ocr_db, OCRStatus.PROCESSING,
                       now_utc() - timedelta(seconds=settings.OCR_STALE_JOB_SECONDS + 1))
    fresh = add_document(ocr_db, OCRStatus.PROCESSING, now_utc())
    finished = add_document(ocr_db, OCRStatus.COMPLETED, now_utc() - timedelta(days=1))
    assert pending in worker.pending_documents()
    with ocr_db[0]() as db:
        assert db.get(OCRDocument, old).ocr_status == OCRStatus.FAILED
        assert "interrupted" in db.get(OCRDocument, old).ocr_error
        assert db.get(OCRDocument, fresh).ocr_status == OCRStatus.PROCESSING
        assert db.get(OCRDocument, finished).ocr_status == OCRStatus.COMPLETED


def test_failed_extraction_is_terminal_with_manual_fallback(ocr_db, monkeypatch):
    identifier = add_document(ocr_db)
    monkeypatch.setattr(service, "run_ocr", lambda **kwargs: "  ")
    service.process_ocr_document_task(identifier)
    with ocr_db[0]() as db:
        doc = db.get(OCRDocument, identifier)
        assert doc.ocr_status == OCRStatus.FAILED
        assert "manual entry" in doc.ocr_error
        assert doc.raw_ocr_text is None and doc.extracted_total_amount is None


def test_ocr_total_deadline_limits_each_call(monkeypatch):
    monkeypatch.setattr(tesseract.time, "monotonic", lambda: 100)
    assert tesseract._remaining_timeout(105) == 5
    with pytest.raises(tesseract.OCRProcessingError):
        tesseract._remaining_timeout(99)
