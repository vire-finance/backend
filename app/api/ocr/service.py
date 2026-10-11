import logging
import uuid
from threading import BoundedSemaphore
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.ocr.model import OCRDocument
from app.api.ocr.parser import parse_ocr_text
from app.api.ocr.repository import OCRRepository
from app.api.ocr.schema import (
    ExtractedData,
    OCRDocumentDetailResponse,
    OCRDocumentUploadResponse,
)
from app.api.ocr.tesseract import run_ocr
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.storage import StorageError, get_receipt_storage, materialize_document
from app.shared.enums import OCRStatus
from app.shared.utils import now_utc


logger = logging.getLogger(__name__)
_processing_slots = BoundedSemaphore(settings.OCR_MAX_CONCURRENT_JOBS)


ALLOWED_EXTENSIONS = {".jpeg", ".png", ".jpg", ".pdf"}

MIME_TYPE_MAPPING = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}


def validate_file_content(
    content: bytes,
    extension: str,
    content_type: str,
) -> None:
    if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
        max_mb = settings.MAX_UPLOAD_SIZE_BYTES // 1048576

        raise HTTPException(
            status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
            detail=f"File size exceeds the maximum limit of {max_mb}MB.",
        )

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unsupported file extension. "
                "Allowed extensions: .jpg, .jpeg, .png, .pdf"
            ),
        )

    normalized_mime = content_type.lower().split(";")[0].strip()

    if normalized_mime not in MIME_TYPE_MAPPING.values():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unsupported MIME type. "
                "Allowed: image/jpeg, image/png, application/pdf"
            ),
        )

    if MIME_TYPE_MAPPING[extension] != normalized_mime:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"MIME type '{normalized_mime}' does not match "
                f"file extension '{extension}'."
            ),
        )

    if extension in {".jpg", ".jpeg"}:
        if not content.startswith(b"\xff\xd8\xff"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JPEG file content.",
            )

        return

    if extension == ".png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid PNG file content.",
            )

        return

    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid PDF file content.",
            )

        return


def process_ocr_document_task(document_id: uuid.UUID) -> None:
    # Both HTTP background work and restart recovery use this atomic claim.
    # Waiting for a local slot happens before opening a database connection.
    with _processing_slots:
        _process_claimed_document(document_id)


def _process_claimed_document(document_id: uuid.UUID) -> None:
    db = SessionLocal()

    try:
        lease_time = now_utc()
        claimed = db.execute(
            update(OCRDocument)
            .where(OCRDocument.id == document_id, OCRDocument.ocr_status == OCRStatus.PENDING)
            .values(ocr_status=OCRStatus.PROCESSING, updated_at=lease_time)
            .returning(OCRDocument.id)
        ).scalar_one_or_none()
        db.commit()
        if claimed is None:
            return
        doc = db.get(OCRDocument, document_id)

        try:
            with materialize_document(doc) as path:
                raw_text = run_ocr(
                    file_path=str(path),
                    mime_type=doc.mime_type,
                    lang="ind+eng",
                )

            if not raw_text.strip():
                raise ValueError("No readable text; use manual entry")
            extracted = parse_ocr_text(raw_text)

            result = dict(
                ocr_status=OCRStatus.COMPLETED,
                raw_ocr_text=raw_text,
                extracted_other_party_name=extracted.other_party_name,
                extracted_total_amount=extracted.total_amount,
                extracted_date=extracted.date,
                ocr_error=None,
            )

        except StorageError as e:
            logger.warning("OCR storage failure document=%s code=%s retryable=%s",
                           document_id, e.code, e.retryable)
            result = dict(
                ocr_status=OCRStatus.PENDING if e.retryable else OCRStatus.FAILED,
                ocr_error=None if e.retryable else "Receipt file is unavailable. Use manual entry or upload again.",
                raw_ocr_text=None, extracted_other_party_name=None,
                extracted_total_amount=None, extracted_date=None,
            )
        except Exception as e:
            logger.warning("OCR failed for document %s (%s)", document_id, type(e).__name__)
            result = dict(
                ocr_status=OCRStatus.FAILED,
                ocr_error="OCR processing failed. Use manual entry or upload a clearer document.",
                raw_ocr_text=None,
                extracted_other_party_name=None,
                extracted_total_amount=None,
                extracted_date=None,
            )

        # A timed-out or superseded lease must never overwrite recovery's result.
        db.execute(
            update(OCRDocument)
            .where(OCRDocument.id == document_id,
                   OCRDocument.ocr_status == OCRStatus.PROCESSING,
                   OCRDocument.updated_at == lease_time)
            .values(**result)
        )
        db.commit()

    except Exception as e:
        logger.warning("OCR task will recover for document %s (%s)", document_id, type(e).__name__)
        db.rollback()

    finally:
        db.close()


class OCRService:
    def __init__(self, db: Session):
        self.db = db
        self.repository = OCRRepository(db)

    async def upload_document(
        self,
        file: UploadFile,
        user_id: uuid.UUID,
    ) -> OCRDocument:
        original_name = Path((file.filename or "unknown").replace("\\", "/")).name[:255]

        ext = Path(original_name).suffix.lower()

        content_type = file.content_type or ""

        content = await file.read(settings.MAX_UPLOAD_SIZE_BYTES + 1)

        validate_file_content(
            content,
            ext,
            content_type,
        )

        doc_id = uuid.uuid4()

        stored_filename = f"{doc_id}{ext}"

        storage = get_receipt_storage()
        normalized_mime = content_type.lower().split(";")[0].strip()
        try:
            storage_path = await run_in_threadpool(storage.save, content, user_id,
                                                  stored_filename, normalized_mime)
        except StorageError as error:
            logger.warning("Receipt upload storage failure document=%s code=%s uncertain=%s",
                           doc_id, error.code, error.uncertain)
            raise

        document = OCRDocument(
            id=doc_id,
            user_id=user_id,
            fund_request_id=None,
            original_filename=original_name,
            stored_filename=stored_filename,
            storage_path=storage_path,
            mime_type=normalized_mime,
            file_size=len(content),
            ocr_status=OCRStatus.PENDING,
        )

        try:
            self.repository.create(document)
            self.db.flush()
        except Exception:
            self._rollback_upload()
            await run_in_threadpool(self._cleanup_upload, storage, storage_path, doc_id)
            raise
        try:
            self.db.commit()
        except Exception:
            self._rollback_upload()
            # A dropped acknowledgement can mean COMMIT succeeded or is still
            # finishing. Even an immediate "row absent" check is not conclusive.
            # Retain the receipt; reconcile uncertain/orphaned objects separately.
            logger.warning("Receipt cleanup deferred document=%s reason=commit_outcome_uncertain", doc_id)
            raise
        # A read failure after successful commit must never delete its receipt.
        self.db.refresh(document)

        return document

    def _rollback_upload(self):
        try:
            self.db.rollback()
        except Exception:
            logger.warning("Receipt upload rollback failed; database diagnostics omitted")

    @staticmethod
    def _cleanup_upload(storage, reference, document_id):
        try:
            storage.delete(reference)
        except Exception:
            logger.warning("Receipt cleanup deferred document=%s reason=storage_unavailable", document_id)

    def get_document_detail(
        self,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> OCRDocumentDetailResponse:
        doc = self.repository.get_by_id(document_id)

        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found.",
            )

        if doc.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this document.",
            )

        if doc.ocr_status in (
            OCRStatus.PENDING,
            OCRStatus.PROCESSING,
        ):
            return OCRDocumentDetailResponse(
                document_id=doc.id,
                status=doc.ocr_status,
            )

        if doc.ocr_status == OCRStatus.COMPLETED:
            return OCRDocumentDetailResponse(
                document_id=doc.id,
                status=doc.ocr_status,
                extracted_data=ExtractedData(
                    other_party_name=doc.extracted_other_party_name,
                    total_amount=doc.extracted_total_amount,
                    date=doc.extracted_date,
                    explanation=(doc.raw_ocr_text or "")[:5000],
                ),
            )

        return OCRDocumentDetailResponse(
            document_id=doc.id,
            status=doc.ocr_status,
            extracted_data=ExtractedData(
                other_party_name=None,
                total_amount=None,
                date=None,
            ),
            message=doc.ocr_error or "OCR processing failed.",
        )
