"""OCR recovery. Pending uploads survive process restarts in PostgreSQL."""
import asyncio
import logging
import re
from datetime import timedelta

from sqlalchemy import select, update

from app.api.ocr import service
from app.api.ocr.model import OCRDocument
from app.core.config import settings
from app.shared.enums import OCRStatus
from app.shared.utils import now_utc

logger = logging.getLogger(__name__)


class OCRRecoveryError(RuntimeError):
    """Carry a recovery stage without rendering database statements or parameters."""

    def __init__(self, stage, original_error):
        super().__init__("OCR recovery operation failed")
        self.stage = stage
        self.original_error = original_error


def _log_recovery_error(error, stage="process_jobs"):
    if isinstance(error, OCRRecoveryError):
        stage = error.stage
        error = error.original_error
    driver_error = getattr(error, "orig", None)
    sqlstate = (
        getattr(driver_error, "sqlstate", None)
        or getattr(driver_error, "pgcode", None)
    )
    if not isinstance(sqlstate, str) or re.fullmatch(r"[A-Z0-9]{5}", sqlstate) is None:
        sqlstate = "unknown"
    hint = (
        "Check PostgreSQL OCR enum labels and Alembic schema consistency."
        if sqlstate == "22P02"
        else "Check the failing recovery stage and database availability."
    )
    # SQLAlchemy and driver messages can contain bound receipt data, SQL and URLs.
    # Log only diagnostic metadata; deliberately omit exception text/tracebacks.
    logger.warning(
        "OCR recovery retry stage=%s error=%s driver_error=%s sqlstate=%s hint=%s",
        stage,
        type(error).__name__,
        type(driver_error).__name__ if driver_error is not None else "unknown",
        sqlstate,
        hint,
    )


def pending_documents():
    stage = "session_open"
    try:
        with service.SessionLocal() as db:
            cutoff = now_utc() - timedelta(seconds=settings.OCR_STALE_JOB_SECONDS)
            stage = "stale_update"
            db.execute(
                update(OCRDocument)
                .where(OCRDocument.ocr_status == OCRStatus.PROCESSING,
                       OCRDocument.updated_at < cutoff)
                .values(ocr_status=OCRStatus.PENDING if settings.APP_ENV == "production" else OCRStatus.FAILED,
                        ocr_error=None if settings.APP_ENV == "production" else "OCR was interrupted. Use manual entry or upload the document again.",
                        raw_ocr_text=None, extracted_other_party_name=None,
                        extracted_total_amount=None, extracted_date=None)
            )
            stage = "stale_commit"
            db.commit()
            stage = "pending_select"
            return list(db.scalars(
                select(OCRDocument.id)
                .where(OCRDocument.ocr_status == OCRStatus.PENDING)
                .order_by(OCRDocument.created_at)
                .limit(settings.OCR_MAX_CONCURRENT_JOBS)
            ).all())
    except Exception as error:
        raise OCRRecoveryError(stage, error) from error


async def run_ocr_worker():
    while True:
        try:
            identifiers = await asyncio.to_thread(pending_documents)
            await asyncio.gather(*(
                asyncio.to_thread(service.process_ocr_document_task, identifier)
                for identifier in identifiers
            ))
        except Exception as error:
            _log_recovery_error(error)
        await asyncio.sleep(5)
