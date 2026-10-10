import uuid
from sqlalchemy import select
from app.api.funding.model import FundAccount, FundMovement
from app.shared.schema import MAX_MONEY
from app.shared.utils import ensure, fields, money

SIMULATED_INITIAL_BALANCE = 20_000_000

class FundingService:
    def __init__(self, ctx):
        self.ctx = ctx

    def account(self, required=True):
        self.ctx.owner_only()
        row = self.ctx.db.scalar(select(FundAccount).where(FundAccount.owner_id == self.ctx.owner_id))
        if required:
            ensure(row is not None, "Siapkan Main Fund Account melalui Linked Fund Account terlebih dahulu.", 409)
        return row

    def view(self, row):
        return {**fields(row, "id", "account_type", "provider_name", "account_number", "account_holder", "balance"), "currency": "IDR", "is_simulated": True}

    def get(self):
        row = self.account(required=False)
        return self.view(row) if row else None

    def save(self, payload):
        row = self.account(required=False)
        if row is None:
            row = FundAccount(owner_id=self.ctx.owner_id, balance=SIMULATED_INITIAL_BALANCE, **payload.model_dump())
            self.ctx.db.add(row)
            self.ctx.db.flush()
            self.record(row, "TOP_UP", SIMULATED_INITIAL_BALANCE)
        else:
            for key, value in payload.model_dump().items():
                setattr(row, key, value)
        self.ctx.user.fund_account_type = payload.account_type
        self.ctx.db.flush()
        return self.ctx.commit(self.view(row))

    def record(self, account, kind, amount, key=None, pocket_id=None):
        row = FundMovement(owner_id=self.ctx.owner_id, account_id=account.id,
            kind=kind, amount=amount, pocket_id=pocket_id,
            idempotency_key=key or uuid.uuid4(), balance_after=account.balance)
        self.ctx.db.add(row)
        self.ctx.db.flush()
        return self.movement_view(row)

    def movement_view(self, row):
        return fields(row, "id", "kind", "amount", "balance_after", "pocket_id", "created_at")

    def history(self):
        self.ctx.owner_only()
        rows = self.ctx.db.scalars(select(FundMovement).where(FundMovement.owner_id == self.ctx.owner_id)
            .order_by(FundMovement.created_at.desc(), FundMovement.id.desc()).limit(100)).all()
        return [self.movement_view(row) for row in rows]

    def allocate(self, payload, key):
        account = self.account()
        pocket = self.ctx.raw_pocket(payload.pocket_id)
        existing = self.ctx.db.scalar(select(FundMovement).where(FundMovement.owner_id == self.ctx.owner_id, FundMovement.idempotency_key == key))
        if existing:
            ensure(existing.kind == "ALLOCATION" and existing.pocket_id == pocket.id and existing.amount == payload.amount, "Idempotency-Key dipakai untuk alokasi berbeda.", 409)
            return self.movement_view(existing)
        ensure(pocket.allocated_amount + payload.amount <= MAX_MONEY, "Saldo pocket melebihi batas sistem.", 409)
        pocket.allocated_amount += payload.amount
        pocket.remaining_amount += payload.amount
        result = self.record(account, "ALLOCATION", payload.amount, key, pocket.id)
        return self.ctx.commit(result)

    def adjust_pocket(self, pocket, delta):
        if not delta:
            return
        account = self.account()
        # Pocket budgets are spending envelopes, not transfers out of the account.
        if delta > 0:
            self.record(account, "ALLOCATION", delta, pocket_id=pocket.id)
        else:
            self.record(account, "RETURN", -delta, pocket_id=pocket.id)
