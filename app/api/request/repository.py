from sqlalchemy import func, select

from app.api.ocr.model import OCRDocument
from app.api.pocket.model import Pocket
from app.api.request.model import FundRequest
from app.shared.enums import FundRequestStatus, UserRole
from app.shared.repository import Repository

class FundRequestRepository(Repository):
    model = FundRequest

    def visible(self, owner_id, user):
        statement = (
            select(FundRequest)
            .join(Pocket, Pocket.id == FundRequest.pocket_id)
            .where(Pocket.owner_id == owner_id)
        )

        if user.role == UserRole.EMPLOYEE:
            statement = statement.where(
                FundRequest.requester_id == user.id
            )
        else:
            statement = statement.where(
                FundRequest.status != FundRequestStatus.DRAFT
            )

        return self.db.scalars(
            statement.order_by(
                FundRequest.created_at.desc(),
                FundRequest.id,
            )
        ).all()

    def documents(self, request_id):
        return self.db.scalars(
            select(OCRDocument)
            .where(OCRDocument.fund_request_id == request_id)
            .order_by(OCRDocument.created_at, OCRDocument.id)
        ).all()

    def document(self, document_id, lock=False):
        statement = select(OCRDocument).where(
            OCRDocument.id == document_id
        )
        if lock:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def comparison(self, row):
        average = self.db.scalar(
            select(func.avg(FundRequest.total_amount)).where(
                FundRequest.pocket_id == row.pocket_id,
                FundRequest.id != row.id,
                FundRequest.status == FundRequestStatus.COMPLETED,
            )
        )

        duplicates = self.db.scalar(
            select(func.count(FundRequest.id)).where(
                FundRequest.pocket_id == row.pocket_id,
                FundRequest.id != row.id,
                func.lower(FundRequest.party_name) == row.party_name.lower(),
                FundRequest.total_amount == row.total_amount,
                FundRequest.status.in_([
                    FundRequestStatus.PENDING_APPROVAL,
                    FundRequestStatus.APPROVED,
                    FundRequestStatus.COMPLETED,
                ]),
            )
        ) or 0

        return average, duplicates