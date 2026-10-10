from sqlalchemy import and_, func, or_, select

from app.api.card.model import Card, CardAccess
from app.api.pocket.model import Pocket, PocketAccess
from app.api.request.model import FundRequest
from app.api.transaction.model import Transaction
from app.shared.enums import UserRole


class HistoryRepository:
    def __init__(self, ctx):
        self.ctx = ctx

    def query(self):
        user = self.ctx.user
        statement = (select(Transaction)
            .join(Pocket, Pocket.id == Transaction.pocket_id)
            .join(Card, Card.id == Transaction.card_id)
            .outerjoin(FundRequest, FundRequest.id == Transaction.fund_request_id)
            .where(Pocket.owner_id == self.ctx.owner_id))
        if user.role == UserRole.EMPLOYEE:
            pocket_access = select(PocketAccess.id).where(
                PocketAccess.pocket_id == Pocket.id,
                PocketAccess.employee_id == user.id).exists()
            card_access = select(CardAccess.id).where(
                CardAccess.card_id == Card.id,
                CardAccess.employee_id == user.id).exists()
            statement = statement.where(
                or_(and_(Transaction.fund_request_id.is_(None), Transaction.actor_id == user.id),
                    FundRequest.requester_id == user.id))
        return statement

    def count(self, statement):
        return self.ctx.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
