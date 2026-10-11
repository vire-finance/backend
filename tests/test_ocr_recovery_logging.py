"""Recovery diagnostics are useful without logging receipt data or DB secrets."""
import asyncio
import logging

import pytest
from sqlalchemy.exc import DataError

from app.api.ocr import service, worker


class InvalidTextRepresentation(Exception):
    pgcode = "22P02"


def sensitive_database_error():
    return DataError(
        "UPDATE private_receipt_table SET raw_ocr_text=:private_receipt",
        {"private_receipt": "PRIVATE_RECEIPT_CONTENT"},
        InvalidTextRepresentation("postgresql://secret_user:SECRET_PASSWORD@private_host/db"),
    )


class FakeSession:
    def __init__(self, failure_stage, error):
        self.failure_stage = failure_stage
        self.error = error
        self.committed = False

    def __enter__(self):
        if self.failure_stage == "session_open":
            raise self.error
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement):
        if self.failure_stage == "stale_update":
            raise self.error

    def commit(self):
        if self.failure_stage == "stale_commit":
            raise self.error
        self.committed = True

    def scalars(self, statement):
        if self.failure_stage == "pending_select":
            raise self.error
        return self

    def all(self):
        return []


async def stop_after_iteration(seconds):
    assert seconds == 5
    raise asyncio.CancelledError


@pytest.mark.parametrize("stage", [
    "session_open", "stale_update", "stale_commit", "pending_select",
])
def test_recovery_logs_safe_stage_and_sqlstate(monkeypatch, caplog, stage):
    session = FakeSession(stage, sensitive_database_error())
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(worker.asyncio, "sleep", stop_after_iteration)

    with caplog.at_level(logging.WARNING, logger=worker.__name__):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(worker.run_ocr_worker())

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert f"stage={stage}" in record.getMessage()
    assert "error=DataError" in record.getMessage()
    assert "driver_error=InvalidTextRepresentation" in record.getMessage()
    assert "sqlstate=22P02" in record.getMessage()
    assert "Check PostgreSQL OCR enum labels" in record.getMessage()
    assert record.exc_info is None
    for private_value in (
        "private_receipt_table", "private_receipt", "PRIVATE_RECEIPT_CONTENT",
        "postgresql://", "secret_user", "SECRET_PASSWORD", "private_host",
    ):
        assert private_value not in caplog.text


def test_recovery_exception_retains_original_without_rendering_it(monkeypatch):
    error = sensitive_database_error()
    monkeypatch.setattr(service, "SessionLocal", lambda: FakeSession("stale_update", error))
    with pytest.raises(worker.OCRRecoveryError) as caught:
        worker.pending_documents()
    assert caught.value.original_error is error
    assert caught.value.stage == "stale_update"
    assert caught.value.__cause__ is error
    assert str(caught.value) == "OCR recovery operation failed"


@pytest.mark.parametrize("sqlstate", [None, "22P02 SECRET_PASSWORD", "postgresql://PRIVATE", 22])
def test_untrusted_sqlstate_is_not_logged(caplog, sqlstate):
    driver_error = InvalidTextRepresentation("PRIVATE_RECEIPT_CONTENT")
    driver_error.pgcode = sqlstate
    driver_error.sqlstate = sqlstate
    error = DataError("PRIVATE_SQL", {"secret": "SECRET_PASSWORD"}, driver_error)
    with caplog.at_level(logging.WARNING, logger=worker.__name__):
        worker._log_recovery_error(error)
    assert "sqlstate=unknown" in caplog.text
    assert "PRIVATE" not in caplog.text
    assert "SECRET_PASSWORD" not in caplog.text
    assert caplog.records[0].exc_info is None


def test_non_database_job_error_omits_sensitive_message(caplog):
    with caplog.at_level(logging.WARNING, logger=worker.__name__):
        worker._log_recovery_error(ValueError("PRIVATE_RECEIPT_CONTENT"))
    assert "stage=process_jobs error=ValueError driver_error=unknown sqlstate=unknown" in caplog.text
    assert "PRIVATE_RECEIPT_CONTENT" not in caplog.text


def test_successful_recovery_iteration_has_no_retry_warning(monkeypatch, caplog):
    session = FakeSession(None, None)
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(worker.asyncio, "sleep", stop_after_iteration)
    with caplog.at_level(logging.WARNING, logger=worker.__name__):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(worker.run_ocr_worker())
    assert session.committed
    assert caplog.records == []
