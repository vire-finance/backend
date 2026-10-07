"""Read-only PostgreSQL aggregation over the existing owner-scoped ledger."""
from sqlalchemy import func, or_, select

from app.api.card.model import Card
from app.api.history.repository import HistoryRepository
from app.api.pocket.model import Pocket
from app.api.transaction.model import Transaction


class AnalyticsRepository:
    def __init__(self, ctx):
        self.ctx = ctx
        self.db = ctx.db
        self.base = HistoryRepository(ctx).query().where(
            Transaction.status == "APPROVED", Card.pocket_id == Transaction.pocket_id)

    @staticmethod
    def between(start, end):
        return (Transaction.processed_at >= start) & (Transaction.processed_at < end)

    def totals(self, start, end):
        return self.db.execute(self.base.with_only_columns(
            func.coalesce(func.sum(Transaction.amount), 0), func.count(Transaction.id)
        ).where(self.between(start, end))).one()

    def grouped(self, column, period, current_start, current_end):
        current = self.between(period.start, period.end)
        previous = self.between(period.previous_start, period.previous_end)
        limit_month = self.between(current_start, current_end)
        return self.db.execute(self.base.with_only_columns(
            column,
            func.coalesce(func.sum(Transaction.amount).filter(current), 0),
            func.count(Transaction.id).filter(current),
            func.coalesce(func.sum(Transaction.amount).filter(previous), 0),
            func.coalesce(func.sum(Transaction.amount).filter(limit_month), 0),
        ).where(or_(current, previous, limit_month)).group_by(column)).all()

    def trend(self, period):
        # processed_at is timestamp without timezone, representing UTC.
        month = func.to_char(func.timezone("Asia/Jakarta", func.timezone("UTC", Transaction.processed_at)), "YYYY-MM")
        return self.db.execute(self.base.with_only_columns(
            month, func.sum(Transaction.amount), func.count(Transaction.id)
        ).where(self.between(period.start, period.end)).group_by(month).order_by(month)).all()

    def pockets(self):
        return self.db.execute(select(Pocket.id, Pocket.name, Pocket.allocated_amount,
            Pocket.remaining_amount, Pocket.monthly_limit).where(Pocket.owner_id == self.ctx.owner_id)).all()

    def cards(self):
        return self.db.execute(select(Card.id, Card.pocket_id, Card.name, Card.category,
            Card.balance, Card.monthly_limit).join(Pocket, Pocket.id == Card.pocket_id)
            .where(Pocket.owner_id == self.ctx.owner_id)).all()

    def top_transactions(self, period, threshold=None, limit=5):
        statement = self.base.with_only_columns(Transaction.id, Transaction.amount, Transaction.processed_at,
            Pocket.id.label("pocket_id"), Pocket.name.label("pocket_name"),
            Card.id.label("card_id"), Card.name.label("card_name"))
        if threshold is not None:
            statement = statement.where(Transaction.amount > threshold)
        return self.db.execute(statement.where(self.between(period.start, period.end))
            .order_by(Transaction.amount.desc(), Transaction.id).limit(limit)).mappings().all()

    def missing_dates(self):
        return self.db.scalar(self.base.with_only_columns(func.count(Transaction.id))
            .where(Transaction.processed_at.is_(None))) or 0
