from sqlalchemy import func, select

from app.api.card.model import Card
from app.api.request.model import FundRequest
from app.api.pocket.model import Pocket
from app.api.transaction.model import Transaction
from app.shared.repository import Repository
from app.shared.utils import money, month_bounds


class TransactionRepository(Repository):
    model = Transaction

    def attempt(self, actor_id, key):
        return self.one(Transaction.actor_id == actor_id, Transaction.idempotency_key == key)

    def locked(self, transaction_id):
        return self.db.scalar(
            select(Transaction)
            .where(Transaction.id == transaction_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def spent(self, pocket_id=None, card_id=None, at=None):
        start, end = month_bounds(at)
        statement = select(
            func.coalesce(func.sum(Transaction.amount), 0)
        ).where(
            Transaction.status == "APPROVED",
            ~select(FundRequest.id).where(FundRequest.id == Transaction.fund_request_id,
                FundRequest.payment_executor == "OWNER_PAYMENT", FundRequest.receipt_status.is_not(None)).exists(),
            Transaction.processed_at >= start,
            Transaction.processed_at < end,
        )

        if pocket_id is not None:
            statement = statement.where(
                Transaction.pocket_id == pocket_id
            )
        if card_id is not None:
            statement = statement.where(Transaction.card_id == card_id)

        return money(self.db.scalar(statement) or 0)

    def history(self, owner_id, pocket_id=None, card_id=None):
        statement = (
            select(Transaction)
            .join(Pocket, Pocket.id == Transaction.pocket_id)
            .where(Pocket.owner_id == owner_id)
        )

        if pocket_id is not None:
            statement = statement.where(
                Transaction.pocket_id == pocket_id
            )
        if card_id is not None:
            statement = statement.where(Transaction.card_id == card_id)

        return self.db.scalars(
            statement.order_by(
                Transaction.created_at.desc(),
                Transaction.id,
            )
        ).all()

    def historical_spending(self, pocket_id, start, end, category=None):
        statement = (
            select(
                func.coalesce(func.sum(Transaction.amount), 0),
                func.count(func.distinct(Transaction.card_id)),
            )
            .join(Card, Card.id == Transaction.card_id)
            .where(
                Transaction.pocket_id == pocket_id,
                Transaction.status == "APPROVED",
                Transaction.processed_at >= start,
                Transaction.processed_at < end,
            )
        )

        if category:
            statement = statement.where(func.coalesce(Transaction.category, Card.category, "Others") == category)

        total, card_count = self.db.execute(statement).one()
        return money(total), card_count