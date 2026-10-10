import csv
import io
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.api.funding.service import FundingService
from app.api.card.model import Card
from app.api.card.repository import CardRepository
from app.api.card.service import CardService
from app.api.history.service import HistoryService
from app.api.notification.service import NotificationService
from app.api.pocket.repository import PocketRepository
from app.api.pocket.service import PocketService
from app.api.request.service import FundRequestService
from app.api.transaction.model import Transaction
from app.shared.enums import FundRequestStatus, UserRole
from app.shared.utils import WIB, ensure, fields, iso, local_time, money, now_utc


def period_bounds(period=None):
    try:
        current = local_time(now_utc())
        year, month = map(int, period.split("-")) if period else (current.year, current.month)
        start = datetime(year, month, 1, tzinfo=WIB)
        end = datetime(year + 1, 1, 1, tzinfo=WIB) if month == 12 else datetime(year, month + 1, 1, tzinfo=WIB)
    except (ValueError, OverflowError):
        ensure(False, "Invalid calendar month.", 422)
    return tuple(value.astimezone(timezone.utc).replace(tzinfo=None) for value in (start, end))


class DashboardService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.history = HistoryService(ctx)

    def summary(self):
        ctx = self.ctx
        pockets = [p for p in PocketRepository(ctx.db).for_owner(ctx.owner_id) if ctx.can_pocket(p)]
        cards = [c for c in CardRepository(ctx.db).for_owner(ctx.owner_id) if ctx.can_card(c)]
        total = sum(money(p.remaining_amount) for p in pockets)
        main_account = None
        if ctx.user.role == UserRole.OWNER:
            main_account = FundingService(ctx).get()
            total = main_account["balance"] if main_account else 0
            pocket_views = [PocketService(ctx).view(p) for p in pockets]
            card_views = [CardService(ctx).view(c) for c in cards]
        else:
            # Shared budgets may be visible, but other employees' spending is private.
            start, end = period_bounds()
            spending = dict(ctx.db.execute(self.history.repo.query()
                .with_only_columns(Transaction.card_id, func.sum(Transaction.amount))
                .where(Transaction.status == "APPROVED", Transaction.processed_at >= start,
                       Transaction.processed_at < end).group_by(Transaction.card_id)).all())
            pocket_ids = {p.id for p in pockets}
            direct_balances = Counter()
            for c in cards:
                if c.pocket_id not in pocket_ids:
                    direct_balances[c.pocket_id] += money(c.balance)
            total += sum(min(value, money(ctx.raw_pocket(key).remaining_amount))
                         for key, value in direct_balances.items())
            pocket_views = [fields(p, "id", "name", "theme", "remaining_amount") for p in pockets]
            card_views = [{**fields(c, "id", "pocket_id", "name", "category", "allowed_categories", "theme", "status", "balance", "monthly_limit", "last_four_digits"),
                           "own_monthly_spent": money(spending.get(c.id, 0)), "is_simulated": True} for c in cards]
        requests = FundRequestService(ctx).list(limit=5)
        result = {"name": ctx.user.full_name or ctx.user.username, "role": ctx.user.role.value,
            "total_balance": total, "currency": "IDR", "is_simulated": True,
            "balance_scope": "BUSINESS" if ctx.user.role == UserRole.OWNER else "ACCESSIBLE_BUDGETS",
            "pockets": pocket_views, "cards": card_views, "request_summary": requests["summary"],
            "own_requests": requests["items"] if ctx.user.role == UserRole.EMPLOYEE else [],
            "pending_request_count": requests["summary"]["waiting"],
            "recent_transactions": self.history.list(limit=5),
            "unread_notification_count": NotificationService(ctx).unread_count()["unread_count"]}
        if ctx.user.role == UserRole.OWNER:
            from app.api.ai.service import AIService
            result["ai_analysis"] = AIService(ctx).anomalies()
            result["main_fund_account"] = main_account
        return result

    def spending(self, period=None, pocket_id=None):
        self.ctx.owner_only()
        if pocket_id:
            self.ctx.raw_pocket(pocket_id)
        start, end = period_bounds(period)
        statement = self.history.repo.query().where(Transaction.status == "APPROVED",
            Transaction.processed_at >= start, Transaction.processed_at < end)
        if pocket_id:
            statement = statement.where(Transaction.pocket_id == pocket_id)
        category_column = func.coalesce(Transaction.category, Card.category, "Others")
        rows = self.ctx.db.execute(statement.with_only_columns(category_column, func.sum(Transaction.amount))
                                   .group_by(category_column)).all()
        total = sum(money(value) for _, value in rows)
        pockets = PocketRepository(self.ctx.db).for_owner(self.ctx.owner_id)
        monthly_limit = sum(money(p.monthly_limit) for p in pockets if not pocket_id or p.id == pocket_id)
        return {"period": local_time(start).strftime("%Y-%m"), "period_start": iso(start), "period_end": iso(end),
            "timezone": "Asia/Jakarta", "pocket_id": pocket_id, "total_spent": total,
            "monthly_limit": monthly_limit, "percentage_used": round(total * 100 / monthly_limit, 2) if monthly_limit else 0,
            "categories": [{"category": category or "Uncategorized", "amount": money(value),
                "percentage": round(money(value) * 100 / total, 2) if total else 0} for category, value in rows]}

    def export(self, period=None, pocket_id=None):
        data = self.spending(period, pocket_id)
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(["period", "category", "amount_idr", "percentage"])
        for item in data["categories"]:
            category = item["category"]
            if category.lstrip().startswith(("=", "+", "-", "@")):
                category = "'" + category
            writer.writerow([data["period"], category, item["amount"], item["percentage"]])
        return buffer.getvalue().encode("utf-8-sig")
