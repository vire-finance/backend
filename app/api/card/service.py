import secrets
from datetime import timezone

from app.api.card.model import Card, CardAccess
from app.api.card.repository import CardRepository
from app.api.transaction.repository import TransactionRepository
from app.shared.enums import CardStatus
from app.shared.utils import ensure, fields, limit_data, local_time, money, month_bounds, now_utc, previous_month

class CardService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.repo = CardRepository(ctx.db)
        self.transactions = TransactionRepository(ctx.db)

    def view(self, row, detail=False):
        pocket = self.ctx.raw_pocket(row.pocket_id)
        result = {
            **fields(
                row, "id", "pocket_id", "name", "category", "allowed_categories",
                "theme", "status", "network", "last_four_digits",
                "balance", "expiry_month", "expiry_year",
                "expires_on", "usage_type", "single_use_consumed_at",
            ),
            "pocket_name": pocket.name,
            "masked_number": f"SIM •••• •••• {row.last_four_digits}",
            "cvv": "***",
            "is_simulated": True,
            **limit_data(
                row.monthly_limit,
                self.transactions.spent(card_id=row.id),
            ),
        }

        if detail:
            result["simulated_card_number"] = (
                f"SIM-{row.id.hex[:12].upper()}-{row.last_four_digits}"
            )
            employees = []

            for employee in self.repo.employees(self.ctx.owner_id):
                inherited = self.ctx.can_pocket(pocket, employee)
                direct = self.repo.access(row.id, employee.id) is not None

                if inherited or direct:
                    employees.append({
                        "id": employee.id,
                        "name": employee.username,
                        "full_name": employee.full_name or employee.username,
                        "avatar_url": employee.avatar_url,
                        "direct_access": direct,
                        "inherited_from_pocket": inherited,
                    })

            result["employees"] = employees

        return result

    def list(self, pocket_id=None, status=None, category=None):
        if pocket_id:
            self.ctx.raw_pocket(pocket_id)

        return [
            self.view(row)
            for row in self.repo.for_owner(self.ctx.owner_id, pocket_id)
            if self.ctx.can_card(row)
            and (status is None or row.status == status)
            and (category is None or row.category == category)
        ]

    def detail(self, card_id):
        return self.view(self.ctx.card(card_id), detail=True)

    def create(self, payload):
        self.ctx.owner_only()
        pocket = self.ctx.raw_pocket(payload.pocket_id)

        ensure(
            payload.monthly_limit <= pocket.remaining_amount,
            "Spending limit melebihi remaining Pocket.",
            409,
        )
        ensure(
            payload.initial_balance <= pocket.remaining_amount,
            "Initial balance melebihi remaining Pocket.",
            409,
        )

        now = local_time(now_utc())

        if payload.expires_on is not None:
            ensure(payload.expires_on >= now.date(), "Tanggal kedaluwarsa tidak boleh di masa lalu.")
        row = self.repo.add(
            Card(
                pocket_id=pocket.id,
                name=payload.name,
                category=payload.category,
                allowed_categories=payload.allowed_categories,
                theme=payload.theme,
                balance=payload.initial_balance,
                spent=0,
                monthly_limit=payload.monthly_limit,
                status=CardStatus.ACTIVE,
                network="VISA",
                last_four_digits=f"{secrets.randbelow(10000):04d}",
                expiry_month=payload.expires_on.month if payload.expires_on else now.month,
                expiry_year=payload.expires_on.year if payload.expires_on else now.year + 3,
                expires_on=payload.expires_on,
                usage_type=payload.usage_type,
            )
        )
        return self.ctx.commit(self.view(row, detail=True))

    def update(self, card_id, payload):
        self.ctx.owner_only()
        row = self.ctx.raw_card(card_id)
        pocket = self.ctx.raw_pocket(row.pocket_id)
        changes = payload.model_dump(exclude_unset=True)

        if "expires_on" in changes:
            expiry = changes["expires_on"]
            ensure(expiry >= local_time(now_utc()).date(), "Tanggal kedaluwarsa tidak boleh di masa lalu.")
            changes["expiry_month"] = expiry.month
            changes["expiry_year"] = expiry.year
        if "usage_type" in changes:
            ensure(row.single_use_consumed_at is None or changes["usage_type"] == row.usage_type,
                   "Kartu single use yang sudah dipakai tidak dapat diubah jenisnya.", 409)

        if "balance" in changes:
            ensure(
                changes["balance"] <= pocket.remaining_amount,
                "Saldo Card melebihi remaining Pocket.",
                409,
            )

        if "monthly_limit" in changes:
            spent = self.transactions.spent(card_id=row.id)
            ensure(
                changes["monthly_limit"] >= spent,
                "Limit tidak boleh lebih kecil dari spending bulan ini.",
                409,
            )
            ensure(
                changes["monthly_limit"] - spent <= pocket.remaining_amount,
                "Sisa limit baru melebihi remaining Pocket.",
                409,
            )

        if "blocked" in changes:
            blocked = changes.pop("blocked")
            if blocked:
                row.status = CardStatus.NONACTIVE
            elif row.status == CardStatus.NONACTIVE:
                row.status = CardStatus.ACTIVE

        for key, value in changes.items():
            setattr(row, key, value)

        self.ctx.db.flush()
        return self.ctx.commit(self.view(row, detail=True))

    def set_status(self, card_id, payload):
        self.ctx.owner_only()
        row = self.ctx.raw_card(card_id)
        ensure(row.status != CardStatus.NONACTIVE or payload.status == CardStatus.NONACTIVE,
               "Buka blokir melalui Card Settings terlebih dahulu.", 409)
        row.status = payload.status
        return self.ctx.commit(self.view(row, detail=True))

    def access(self, card_id, employee_id, grant):
        self.ctx.owner_only()
        row = self.ctx.raw_card(card_id)
        employee = self.ctx.employee(employee_id)
        existing = self.repo.access(card_id, employee_id)

        if grant and existing is None:
            self.ctx.db.add(
                CardAccess(card_id=card_id, employee_id=employee_id)
            )
        elif not grant and existing is not None:
            self.ctx.db.delete(existing)

        inherited = self.ctx.can_pocket(
            self.ctx.raw_pocket(row.pocket_id), employee
        )

        return self.ctx.commit({
            "direct_access": grant,
            "inherited_from_pocket": inherited,
            "effective_access": grant or inherited,
        })

    def recommendation(self, pocket_id, category=None):
        self.ctx.owner_only()
        pocket = self.ctx.raw_pocket(pocket_id)

        end, _ = month_bounds()
        start = local_time(end)

        for _ in range(3):
            start = previous_month(start)

        start = start.astimezone(timezone.utc).replace(tzinfo=None)

        total, count = self.transactions.historical_spending(
            pocket_id, start, end, category
        )

        if total:
            recommendation = max(1, total // (3 * max(1, count)))
            method = "Rata-rata spending per Card per bulan selama tiga bulan kalender terakhir."
        else:
            recommendation = max(1, money(pocket.remaining_amount) // 5)
            method = "Histori belum cukup; menggunakan 20% remaining Pocket."

        monthly_remaining = max(
            0,
            money(pocket.monthly_limit) - self.transactions.spent(pocket_id=pocket_id),
        )

        return {
            "recommended_limit": min(
                recommendation,
                money(pocket.remaining_amount),
                monthly_remaining,
            ),
            "method": "HEURISTIC",
            "explanation": method,
            "category": category,
            "is_binding": False,
        }