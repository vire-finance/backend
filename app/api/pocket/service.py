import csv
import io
from collections import Counter

from app.api.funding.service import FundingService
from app.api.card.service import CardService
from app.api.pocket.model import Pocket, PocketAccess
from app.api.pocket.repository import PocketRepository
from app.api.transaction.repository import TransactionRepository
from app.shared.utils import ensure, fields, iso, limit_data, local_time, money, month_bounds

class PocketService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.repo = PocketRepository(ctx.db)
        self.transactions = TransactionRepository(ctx.db)

    def view(self, row, detail=False):
        start, end = month_bounds()
        result = {
            **fields(
                row, "id", "owner_id", "name", "theme",
                "allocated_amount", "remaining_amount",
            ),
            "card_count": self.repo.card_count(row.id),
            "period_start": iso(start),
            "period_end": iso(end),
            **limit_data(
                row.monthly_limit,
                self.transactions.spent(pocket_id=row.id),
            ),
        }

        if detail:
            result["cards"] = CardService(self.ctx).list(pocket_id=row.id)

        return result

    def list(self):
        return [
            self.view(row)
            for row in self.repo.for_owner(self.ctx.owner_id)
            if self.ctx.can_pocket(row)
        ]

    def detail(self, pocket_id):
        return self.view(self.ctx.pocket(pocket_id), detail=True)

    def create(self, payload):
        self.ctx.owner_only()
        row = self.repo.add(
            Pocket(
                owner_id=self.ctx.owner_id,
                name=payload.name,
                theme=payload.theme,
                allocated_amount=payload.allocated_amount,
                remaining_amount=payload.allocated_amount,
                monthly_limit=(
                    payload.monthly_limit
                    if payload.monthly_limit is not None
                    else payload.allocated_amount
                ),
            )
        )
        FundingService(self.ctx).adjust_pocket(row, payload.allocated_amount)
        return self.ctx.commit(self.view(row))

    def update(self, pocket_id, payload):
        self.ctx.owner_only()
        row = self.ctx.raw_pocket(pocket_id)
        changes = payload.model_dump(exclude_unset=True)

        if "allocated_amount" in changes:
            used = row.allocated_amount - row.remaining_amount
            ensure(
                changes["allocated_amount"] >= used,
                "Budget baru lebih kecil dari dana yang sudah digunakan.",
                409,
            )
            FundingService(self.ctx).adjust_pocket(row, changes["allocated_amount"] - row.allocated_amount)
            row.remaining_amount = changes["allocated_amount"] - used

        if "monthly_limit" in changes:
            ensure(
                changes["monthly_limit"]
                >= self.transactions.spent(pocket_id=row.id),
                "Limit tidak boleh di bawah spending bulan ini.",
                409,
            )

        for key, value in changes.items():
            setattr(row, key, value)

        self.ctx.db.flush()
        return self.ctx.commit(self.view(row))

    def employees(self, pocket_id=None):
        self.ctx.owner_only()
        if pocket_id:
            self.ctx.raw_pocket(pocket_id)

        return [
            {"id": row.id, "name": row.username}
            for row in self.repo.employees(self.ctx.owner_id, pocket_id)
        ]

    def access(self, pocket_id, employee_id, grant):
        self.ctx.owner_only()
        self.ctx.raw_pocket(pocket_id)
        self.ctx.employee(employee_id)
        existing = self.repo.access(pocket_id, employee_id)

        if grant and existing is None:
            self.ctx.db.add(
                PocketAccess(
                    pocket_id=pocket_id,
                    employee_id=employee_id,
                )
            )
        elif not grant and existing is not None:
            self.ctx.db.delete(existing)

        return self.ctx.commit({
            "pocket_access": grant,
            "note": (
                "Akses Card langsung tetap berlaku jika sebelumnya diberikan."
                if not grant else None
            ),
        })

    def analysis(self, pocket_id):
        self.ctx.owner_only()
        pocket = self.ctx.raw_pocket(pocket_id)
        rows = self.transactions.history(self.ctx.owner_id, pocket_id)

        monthly = Counter()
        by_card = Counter()
        counts = Counter(row.status for row in rows)

        for row in rows:
            if row.status != "APPROVED":
                continue
            month = local_time(row.processed_at).strftime("%Y-%m")
            monthly[month] += money(row.amount)
            by_card[str(row.card_id)] += money(row.amount)

        return {
            "pocket": self.view(pocket),
            "monthly_spending": [
                {"month": month, "amount": amount}
                for month, amount in sorted(monthly.items())
            ],
            "spending_by_card": [
                {"card_id": key, "amount": amount}
                for key, amount in by_card.items()
            ],
            "transaction_status_counts": dict(counts),
            "timezone": "Asia/Jakarta",
        }

    def report(self, pocket_id):
        self.ctx.owner_only()
        self.ctx.raw_pocket(pocket_id)
        rows = self.transactions.history(self.ctx.owner_id, pocket_id)

        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow([
            "transaction_id", "created_at_utc", "processed_at_utc",
            "card_id", "fund_request_id", "amount_idr",
            "status", "description", "failure_reason",
        ])

        def safe(value):
            value = "" if value is None else str(value)
            if value.lstrip().startswith(("=", "+", "-", "@")):
                return "'" + value
            return value

        for row in rows:
            writer.writerow([
                row.id, iso(row.created_at), iso(row.processed_at),
                row.card_id, row.fund_request_id or "",
                money(row.amount), row.status,
                safe(row.description), safe(row.failure_reason),
            ])

        return buffer.getvalue().encode("utf-8-sig")