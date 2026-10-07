import logging
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
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
from app.shared.enums import OCRStatus


logger = logging.getLogger(__name__)


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
    db = SessionLocal()

    try:
        repo = OCRRepository(db)
        doc = repo.get_by_id(document_id)

        if not doc:
            logger.warning(
                "Document %s not found for OCR background processing.",
                document_id,
            )
            return

        doc.ocr_status = OCRStatus.PROCESSING
        db.commit()

        try:
            raw_text = run_ocr(
                file_path=doc.storage_path,
                mime_type=doc.mime_type,
                lang="ind+eng",
            )

            if not raw_text.strip():
                raise ValueError("No readable text; use manual entry")
            extracted = parse_ocr_text(raw_text)

            doc.ocr_status = OCRStatus.COMPLETED
            doc.raw_ocr_text = raw_text
            doc.extracted_other_party_name = extracted.other_party_name
            doc.extracted_total_amount = extracted.total_amount
            doc.extracted_date = extracted.date
            doc.ocr_error = None

            db.commit()

        except Exception as e:
            logger.exception(
                "OCR processing failed for document %s: %s",
                document_id,
                e,
            )

            doc.ocr_status = OCRStatus.FAILED
            doc.ocr_error = "OCR processing failed."
            doc.extracted_other_party_name = None
            doc.extracted_total_amount = None
            doc.extracted_date = None

            db.commit()

    except Exception as e:
        logger.exception(
            "Unexpected error in OCR background task for document %s: %s",
            document_id,
            e,
        )
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

        upload_dir = os.path.abspath(settings.UPLOAD_DIR)

        os.makedirs(
            upload_dir,
            exist_ok=True,
        )

        storage_path = os.path.join(
            upload_dir,
            stored_filename,
        )

        with open(storage_path, "wb") as f:
            f.write(content)

        document = OCRDocument(
            id=doc_id,
            user_id=user_id,
            fund_request_id=None,
            original_filename=original_name,
            stored_filename=stored_filename,
            storage_path=storage_path,
            mime_type=content_type.lower().split(";")[0].strip(),
            file_size=len(content),
            ocr_status=OCRStatus.PENDING,
        )

        try:
            self.repository.create(document)
            self.db.commit()
            self.db.refresh(document)
        except Exception:
            self.db.rollback()
            Path(storage_path).unlink(missing_ok=True)
            raise

        return document

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
