import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.ocr.model import OCRDocument


class OCRRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(
        self,
        document_id: uuid.UUID,
    ) -> OCRDocument | None:
        return self.db.get(OCRDocument, document_id)

    def create(
        self,
        document: OCRDocument,
    ) -> OCRDocument:
        self.db.add(document)
        return document

    def list_by_user(
        self,
        user_id: uuid.UUID,
    ) -> list[OCRDocument]:
        stmt = (
            select(OCRDocument)
            .where(OCRDocument.user_id == user_id)
            .order_by(OCRDocument.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())