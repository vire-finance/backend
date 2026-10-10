import io

import qrcode
import qrcode.image.svg

from app.api.funding.service import FundingService
from app.api.payment.model import TopUp
from app.api.payment.repository import InvoiceRepository, PaymentRepository
from app.shared.schema import MAX_MONEY
from app.shared.utils import ensure, fields, money, now_utc


class PaymentService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.repo = PaymentRepository(ctx.db)
        self.invoices = InvoiceRepository(ctx.db)

    def view(self, row):
        data = fields(
            row, "id", "pocket_id", "amount", "method",
            "status", "created_at", "completed_at",
        )
        data.update(
            is_simulated=True,
            qr_image_url=(
                f"/payments/{row.id}/qr"
                if row.method == "QR_CODE" else None
            ),
            bank_details=(
                {
                    "bank_name": "VIRE SIMULATION",
                    "reference": f"SIM-{row.id.hex[:12].upper()}",
                    "account_name": "NO REAL BANK TRANSFER",
                }
                if row.method == "BANK_TRANSFER" else None
            ),
        )
        return data

    def get(self, payment_id):
        self.ctx.owner_only()
        row = self.repo.get(payment_id)
        ensure(
            row is not None and row.owner_id == self.ctx.owner_id,
            "Payment tidak ditemukan.",
            404,
        )
        return row

    def create(self, payload, key):
        self.ctx.owner_only()
        ensure(payload.pocket_id is None, "Top-up baru harus masuk ke Main Fund Account, lalu alokasikan ke pocket.", 422)
        FundingService(self.ctx).account()

        row = self.repo.by_key(self.ctx.owner_id, key)

        if row:
            ensure(
                row.pocket_id == payload.pocket_id
                and row.amount == payload.amount
                and row.method == payload.method,
                "Idempotency-Key sudah dipakai untuk payload berbeda.",
                409,
            )
            return self.view(row)

        row = self.repo.add(
            TopUp(
                owner_id=self.ctx.owner_id,
                pocket_id=payload.pocket_id,
                idempotency_key=key,
                amount=payload.amount,
                method=payload.method,
                status="PENDING",
            )
        )
        return self.ctx.commit(self.view(row))

    def list(self):
        self.ctx.owner_only()
        rows = self.repo.for_owner(self.ctx.owner_id)
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        return [self.view(row) for row in rows]

    def confirm(self, payment_id):
        row = self.get(payment_id)

        if row.status == "COMPLETED":
            return self.view(row)

        if row.pocket_id is None:
            funding = FundingService(self.ctx)
            account = funding.account()
            ensure(account.balance + row.amount <= MAX_MONEY, "Saldo utama melebihi batas sistem.", 409)
            account.balance += row.amount
            row.status = "COMPLETED"
            row.completed_at = now_utc()
            funding.record(account, "TOP_UP", row.amount, key=row.idempotency_key)
            return self.ctx.commit(self.view(row))

        # Pending top-ups created before this migration retain their destination.
        pocket = self.ctx.raw_pocket(row.pocket_id)

        ensure(
            pocket.allocated_amount + row.amount <= MAX_MONEY,
            "Top-up melebihi batas nominal sistem.",
            409,
        )

        pocket.allocated_amount += row.amount
        pocket.remaining_amount += row.amount
        row.status = "COMPLETED"
        row.completed_at = now_utc()

        return self.ctx.commit(self.view(row))

    def qr(self, payment_id):
        row = self.get(payment_id)
        ensure(row.method == "QR_CODE", "Payment bukan QR.")

        content = (
            f"VIRE-SIMULATION|{row.id}|"
            f"{money(row.amount)}|NO-REAL-PAYMENT"
        )
        image = qrcode.make(
            content,
            image_factory=qrcode.image.svg.SvgPathImage,
        )
        buffer = io.BytesIO()
        image.save(buffer)
        return buffer.getvalue()

    def list_invoices(self):
        self.ctx.owner_only()
        return [
            {
                **fields(
                    row, "id", "transaction_id", "request_id",
                    "status", "created_at", "simulated_paid_at",
                ),
                "is_simulated": True,
            }
            for row in self.invoices.for_owner(self.ctx.owner_id)
        ]

    def confirm_invoice(self, payment_id):
        self.ctx.owner_only()
        row = self.invoices.get(payment_id)

        ensure(
            row is not None and row.owner_id == self.ctx.owner_id,
            "Invoice payment tidak ditemukan.",
            404,
        )

        if row.status == "QUEUED":
            row.status = "SIMULATED_PAID"
            row.simulated_paid_at = now_utc()

        # Tidak mengurangi saldo lagi
        return self.ctx.commit({
            **fields(row, "id", "status", "simulated_paid_at"),
            "is_simulated": True,
        })