"""Owner financial numbers come exclusively from the existing ledger/snapshots."""
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.api.ai.periods import local_time, resolve_period, shift_month, utc_naive
from app.api.ai.repository import AnalyticsRepository
from app.api.ai.rules import FinancialRules
from app.api.ai.schema import (
    AnalyticsResponse, BudgetSnapshot, CardBreakdown, CategoryBreakdown, DataAvailability,
    PocketBreakdown, Ranking, SpendingSummary, TopSpending, TopTransaction, TrendPoint,
)
from app.shared.enums import UserRole
from app.shared.utils import money, month_bounds, now_utc


def rounded(value):
    return float(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def percentage(value, total):
    return rounded(Decimal(value) * 100 / Decimal(total)) if total else None


def change(value, previous):
    return percentage(value - previous, previous)


class AnalyticsService:
    def __init__(self, ctx):
        self.ctx = ctx

    def analyze(self, period=None, start_date=None, end_date=None):
        self.ctx.owner_only()
        now = now_utc()
        window = resolve_period(period, start_date, end_date, now=now)
        owner_id = self.ctx.owner_id
        bind = self.ctx.db.get_bind()
        # These endpoints never mutate finance data. Release the existing Context's
        # POST owner lock before reading or waiting on inference.
        self.ctx.db.rollback()
        # All totals/breakdowns share one PostgreSQL snapshot, even during payments.
        with bind.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                with Session(bind=connection) as db:
                    snapshot_ctx = SimpleNamespace(db=db, owner_id=owner_id,
                                                   user=SimpleNamespace(role=UserRole.OWNER))
                    return self._calculate(AnalyticsRepository(snapshot_ctx), window, now)

    def _calculate(self, repo, period, now):
        current_start, current_end = month_bounds(now)
        total_raw, count = repo.totals(period.start, period.end)
        previous_raw, previous_count = repo.totals(period.previous_start, period.previous_end)
        total, previous = money(total_raw), money(previous_raw)
        summary = SpendingSummary(total_spending=total, previous_period_spending=previous,
            absolute_difference=total - previous, change_percentage=change(total, previous),
            comparison_status="comparable" if previous else "no_previous_spending",
            transaction_count=count, previous_transaction_count=previous_count,
            average_transaction_amount=rounded(Decimal(total) / count) if count else 0)

        def grouped(column):
            return {row[0]: (money(row[1]), row[2], money(row[3]), money(row[4]))
                    for row in repo.grouped(column, period, current_start, current_end)}

        from app.api.card.model import Card
        from app.api.transaction.model import Transaction
        pocket_totals = grouped(Transaction.pocket_id)
        card_totals = grouped(Transaction.card_id)
        category_totals = grouped(Card.category)

        def breakdown(values):
            amount, tx_count, old, _ = values
            return dict(amount=amount, transaction_count=tx_count, previous_period_amount=old,
                        change_percentage=change(amount, old), percentage=percentage(amount, total) or 0)

        pockets = []
        for row in repo.pockets():
            values = pocket_totals.get(row.id, (0, 0, 0, 0))
            allocated, remaining, limit = map(money, (row.allocated_amount, row.remaining_amount, row.monthly_limit))
            pockets.append(PocketBreakdown(id=row.id, name=row.name, **breakdown(values),
                current_allocated_amount=allocated, current_remaining_amount=remaining,
                current_budget_utilization_percentage=percentage(allocated - remaining, allocated),
                current_monthly_limit=limit, current_month_spent=values[3],
                current_month_remaining_limit=max(0, limit - values[3]),
                current_month_limit_utilization_percentage=percentage(values[3], limit)))
        cards = []
        for row in repo.cards():
            values = card_totals.get(row.id, (0, 0, 0, 0))
            limit = money(row.monthly_limit)
            cards.append(CardBreakdown(id=row.id, pocket_id=row.pocket_id, name=row.name,
                category=row.category, **breakdown(values), current_balance=money(row.balance),
                current_monthly_limit=limit, current_month_spent=values[3],
                current_month_remaining_limit=max(0, limit - values[3]),
                current_month_limit_utilization_percentage=percentage(values[3], limit)))
        categories = [CategoryBreakdown(category=key, label=key if key is not None else "Uncategorized",
                                       **breakdown(value)) for key, value in category_totals.items()]
        pockets.sort(key=lambda r: (-r.amount, str(r.id)))
        cards.sort(key=lambda r: (-r.amount, str(r.id)))
        categories.sort(key=lambda r: (-r.amount, r.category is None, r.category or ""))

        trend_rows = {key: (money(amount), tx_count) for key, amount, tx_count in repo.trend(period)}
        month = local_time(period.start).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        trend = []
        while utc_naive(month) < period.end:
            key = month.strftime("%Y-%m")
            amount, tx_count = trend_rows.get(key, (0, 0))
            trend.append(TrendPoint(period=key, amount=amount, transaction_count=tx_count))
            month = shift_month(month, 1)

        allocated = sum(p.current_allocated_amount for p in pockets)
        remaining = sum(p.current_remaining_amount for p in pockets)
        limit = sum(p.current_monthly_limit for p in pockets)
        limit_spent = sum(p.current_month_spent for p in pockets)
        budget = BudgetSnapshot(as_of=now.replace(tzinfo=timezone.utc), allocated_amount=allocated,
            remaining_amount=remaining, used_amount=allocated - remaining,
            utilization_percentage=percentage(allocated - remaining, allocated),
            current_limit_period=local_time(current_start).strftime("%Y-%m"), monthly_limit=limit,
            current_month_spent=limit_spent,
            current_month_remaining_limit=sum(p.current_month_remaining_limit for p in pockets),
            current_month_limit_utilization_percentage=percentage(limit_spent, limit))

        baseline_end = local_time(period.start).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        baseline_start = shift_month(baseline_end, -3)
        baseline_total, baseline_count = repo.totals(utc_naive(baseline_start), utc_naive(baseline_end))
        baseline_average = Decimal(baseline_total) / baseline_count if baseline_count else None
        rules = FinancialRules()
        large = (repo.top_transactions(period, baseline_average * Decimal(str(rules.large_multiplier)), limit=10)
                 if baseline_count >= rules.min_baseline else [])
        availability = DataAvailability(approved_transactions_missing_processed_at=repo.missing_dates(),
            baseline_transaction_count=baseline_count,
            baseline_average_transaction_amount=rounded(baseline_average) if baseline_average is not None else None,
            baseline_history_sufficient=baseline_count >= rules.min_baseline)
        signals = rules.evaluate(summary, budget, pockets, cards, categories, large, availability)
        def ranking(rows, category=False):
            return [Ranking(id=None if category else row.id,
                            name=row.label if category else row.name, amount=row.amount)
                    for row in rows if row.amount > 0][:5]

        top = TopSpending(pockets=ranking(pockets), cards=ranking(cards),
            categories=ranking(categories, category=True),
            transactions=[TopTransaction(**{**r, "processed_at": r["processed_at"].replace(tzinfo=timezone.utc)})
                          for r in repo.top_transactions(period)])
        return AnalyticsResponse(period=period.window(), previous_period=period.window(previous=True),
            summary=summary, monthly_trend=trend, pocket_breakdown=pockets, card_breakdown=cards,
            category_breakdown=categories, budget=budget, top_spending=top,
            signals=signals, overall_status=rules.overall_status(signals), data_availability=availability)
