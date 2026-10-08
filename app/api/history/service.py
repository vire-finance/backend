from datetime import timedelta, timezone

from sqlalchemy import or_

from app.api.card.model import Card
from app.api.request.model import FundRequest
from app.api.transaction.model import Transaction
from app.api.transaction.service import TransactionService
from app.api.history.repository import HistoryRepository
from app.shared.utils import ensure, local_time, money, now_utc


def utc_filter(value):
    if value is None:
        return None
    ensure(value.tzinfo is not None, "Date filters must include a timezone.")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class HistoryService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.repo = HistoryRepository(ctx)

    def filtered(self, pocket_id=None, card_id=None, status=None, search="", category=None,
                 date_from=None, date_to=None):
        if pocket_id:
            self.ctx.pocket(pocket_id)
        if card_id:
            self.ctx.card(card_id)
        start, end = utc_filter(date_from), utc_filter(date_to)
        ensure(not (start and end) or start < end, "Invalid date range.")
        statement = self.repo.query()
        conditions = []
        if pocket_id:
            conditions.append(Transaction.pocket_id == pocket_id)
        if card_id:
            conditions.append(Transaction.card_id == card_id)
        if status:
            conditions.append(Transaction.status == status)
        if category:
            conditions.append(Card.category == category)
        if start:
            conditions.append(Transaction.created_at >= start)
        if end:
            conditions.append(Transaction.created_at < end)
        if search.strip():
            # Escape user wildcards; a search is a literal substring.
            term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = "%" + term + "%"
            conditions.append(or_(Transaction.description.ilike(pattern, escape="\\"),
                FundRequest.party_name.ilike(pattern, escape="\\"), Card.name.ilike(pattern, escape="\\")))
        return statement.where(*conditions)

    def view(self, row):
        result = TransactionService(self.ctx).view(row)
        card = self.ctx.raw_card(row.card_id)
        request = self.ctx.db.get(FundRequest, row.fund_request_id) if row.fund_request_id else None
        day = local_time(row.created_at).date()
        today = local_time(now_utc()).date()
        if day == today:
            group = "Today"
        elif today - timedelta(days=today.weekday()) <= day < today:
            group = "This Week"
        elif (day.year, day.month) == (today.year, today.month):
            group = "This Month"
        else:
            group = day.strftime("%Y-%m")
        result.update(card_name=card.name, category=card.category or "Uncategorized",
            party_name=request.party_name if request else row.description,
            period_group=group, direction="OUTGOING", signed_amount=-money(row.amount),
            balance_effect=-money(row.amount) if row.status == "APPROVED" else 0)
        return result

    def list(self, offset=0, limit=50, **filters):
        statement = self.filtered(**filters)
        rows = self.ctx.db.scalars(statement.order_by(Transaction.created_at.desc(), Transaction.id)
                                   .offset(offset).limit(limit)).all()
        return {"total": self.repo.count(statement), "offset": offset, "limit": limit,
                "timezone": "Asia/Jakarta", "items": [self.view(row) for row in rows]}

    def detail(self, transaction_id):
        row = self.ctx.db.scalar(self.repo.query().where(Transaction.id == transaction_id))
        ensure(row is not None, "Transaction not found.", 404)
        return self.view(row)
