import hashlib
import json
import logging

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.funding.model import FundAccount
from app.api.funding.service import FundingService

from app.api.auth.model import User
from app.api.notification.service import NotificationService
from app.api.payment.model import InvoicePayment
from app.api.transaction.model import Transaction
from app.api.transaction.repository import TransactionRepository
from app.shared.enums import CardStatus, FundRequestStatus, TransactionType, UserRole
from app.shared.utils import ensure, fields, local_time, now_utc

logger = logging.getLogger(__name__)

class Declined(Exception):
    pass

def require(condition, reason):
    if not condition:
        raise Declined(reason)

class TransactionService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.repo = TransactionRepository(ctx.db)

    def view(self, row):
        return {
            **fields(
                row, "id", "pocket_id", "card_id",
                "fund_request_id", "actor_id", "amount",
                "transaction_type", "status", "description", "category",
                "failure_reason", "created_at", "processed_at", "payment_method", "recipient_account",
            ),
            "is_simulated": True,
        }

    def execute(self, card_id, amount, description, key, request_id=None, category=None, payment_method="QRIS", recipient_account=None):
        ctx = self.ctx
        card = ctx.card(card_id)

        if payment_method in ("BANK_TRANSFER", "E_WALLET"):
            ensure(bool(recipient_account and recipient_account.strip()), "Tujuan pembayaran wajib diisi.")

        if request_id:
            request = ctx.request(request_id)
            owner_payment = request.payment_executor == "OWNER_PAYMENT"
            if owner_payment:
                ctx.owner_only()
                from app.api.request.repository import FundRequestRepository
                ensure(bool(FundRequestRepository(ctx.db).documents(request.id)), "Invoice wajib tersedia.", 409)
            else:
                ensure(False, "Pembayaran request invoice hanya dapat dilakukan owner. Gunakan Payment untuk transaksi langsung.", 409)
            ensure(request.pocket_id == card.pocket_id and request.card_id in (None, card.id), "Kartu berbeda dari request.", 409)
            ensure(category in (None, request.category) and description == request.explanation[:255], "Detail pembelian berbeda dari request.", 409)
            ensure(payment_method == request.payment_method and recipient_account == request.recipient_account, "Tujuan/metode pembayaran berbeda dari request.", 409)

        # Request expenses always retain the category chosen by the employee.
        if request_id:
            category = request.category

        kind = (
            TransactionType.FUND_REQUEST
            if request_id
            else TransactionType.CARD_PAYMENT
        )

        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "card": str(card_id),
                    "request": str(request_id) if request_id else None,
                    "amount": amount,
                    "payment_method": payment_method,
                    "recipient_account": recipient_account,
                    "description": description,
                    "type": kind.value,
                    **({"category": category} if category is not None else {}),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

        row = self.repo.attempt(ctx.user.id, key)

        if row:
            ensure(
                row.fingerprint == fingerprint,
                "Idempotency-Key sudah dipakai untuk payload berbeda.",
                409,
            )
            if row.status != "PENDING":
                return self.view(row)
        else:
            row = self.repo.add(
                Transaction(
                    pocket_id=card.pocket_id,
                    card_id=card.id,
                    fund_request_id=request_id,
                    actor_id=ctx.user.id,
                    idempotency_key=key,
                    fingerprint=fingerprint,
                    amount=amount,
                    payment_method=payment_method,
                    recipient_account=recipient_account,
                    transaction_type=kind,
                    status="PENDING",
                    description=description,
                    category=category or card.category or "Others",
                )
            )

            # Durable attempt sebelum proses debit.
            ctx.db.commit()

        transaction_id = row.id

        # Commit di atas melepas lock. Ambil kembali, lalu muat ulang data untuk menghindari saldo stale.
        ctx.lock()
        ctx.db.expire_all()
        row = self.repo.locked(transaction_id)

        if row.status != "PENDING":
            return self.view(row)

        try:
            with ctx.db.begin_nested():
                card = ctx.raw_card(card_id)
                pocket = ctx.raw_pocket(card.pocket_id)
                request = None

                require(
                    ctx.can_card(card),
                    "Akses Card telah dicabut.",
                )

                if request_id:
                    request = ctx.request(request_id)

                    owner_payment = request.payment_executor == "OWNER_PAYMENT"
                    require((owner_payment and ctx.user.role == UserRole.OWNER) or (not owner_payment and request.requester_id == ctx.user.id), "Pelaksana pembayaran tidak sesuai request.")
                    require(
                        request.status == (FundRequestStatus.PENDING_APPROVAL if owner_payment else FundRequestStatus.APPROVED) and request.paid_at is None,
                        "Request belum disetujui atau sudah dibayar.",
                    )
                    require(
                        request.pocket_id == pocket.id
                        and request.total_amount == amount,
                        "Data Request tidak cocok dengan transaksi.",
                    )

                    requester = ctx.db.get(User, request.requester_id)
                    require(
                        requester is not None
                        and ctx.can_card(card, requester),
                        "Requester tidak lagi memiliki akses Pocket.",
                    )

                budget_exception = bool(request and request.payment_executor == "OWNER_PAYMENT")
                if not budget_exception and (category is not None or ctx.user.role == UserRole.EMPLOYEE):
                    require(row.category in card.allowed_categories,
                            "Kategori pengeluaran tidak diizinkan untuk kartu ini.")

                now = now_utc()
                local = local_time(now)

                if not budget_exception:
                    require(
                        card.status == CardStatus.ACTIVE,
                        "Card frozen atau nonaktif.",
                    )
                    require(
                        (card.expiry_year, card.expiry_month)
                        >= (local.year, local.month),
                        "Card kedaluwarsa.",
                    )
                    require(card.expires_on is None or card.expires_on >= local.date(), "Card kedaluwarsa.")
                    require(card.single_use_consumed_at is None, "Kartu single use sudah digunakan.")
                if not budget_exception:
                    require(
                        pocket.remaining_amount >= amount,
                        "Remaining budget Pocket tidak cukup.",
                    )
                    require(card.balance >= amount, "Saldo Card tidak cukup.")
                    require(
                        self.repo.spent(card_id=card.id, at=now) + amount
                        <= card.monthly_limit,
                        "Monthly limit Card terlampaui.",
                    )
                    require(
                        self.repo.spent(pocket_id=pocket.id, at=now) + amount
                        <= pocket.monthly_limit,
                        "Monthly limit Pocket terlampaui.",
                    )
                account = ctx.db.scalar(select(FundAccount).where(
                    FundAccount.owner_id == ctx.owner_id).with_for_update())
                require(account is not None, "Main Fund Account belum disiapkan.")
                require(account.balance >= amount, "Saldo utama tidak cukup untuk transaksi ini.")

                account.balance -= Decimal(amount)
                FundingService(ctx).record(account, "PAYMENT", amount, key=row.id, pocket_id=pocket.id)
                if not budget_exception:
                    pocket.remaining_amount -= Decimal(amount)
                    card.balance -= Decimal(amount)

                # Kolom legacy; monthly usage tetap dihitung dari ledger.
                if not budget_exception:
                    card.spent += Decimal(amount)
                if not budget_exception and card.usage_type == "SINGLE_USE":
                    card.single_use_consumed_at = now

                row.status = "APPROVED"
                row.failure_reason = None
                row.processed_at = now

                # Direct employee payments create their receipt record only after
                # a successful debit, in the same database transaction.
                if request is None and ctx.user.role == UserRole.EMPLOYEE:
                    from app.api.request.model import FundRequest
                    request = FundRequest(
                        requester_id=ctx.user.id,
                        pocket_id=pocket.id,
                        card_id=card.id,
                        payment_executor="EMPLOYEE_PAYMENT",
                        payment_method=payment_method,
                        recipient_account=recipient_account,
                        explanation=description,
                        category=row.category,
                        party_name=description[:150],
                        total_amount=amount,
                        needed_by=now,
                        status=FundRequestStatus.APPROVED,
                    )
                    ctx.db.add(request)
                    ctx.db.flush()
                    row.fund_request_id = request.id

                if request:
                    from datetime import timedelta
                    request.card_id = card.id
                    request.status = FundRequestStatus.APPROVED
                    if budget_exception:
                        request.reviewed_by = ctx.user.id
                        request.reviewed_at = now
                    request.paid_at = now
                    if budget_exception:
                        request.status = FundRequestStatus.COMPLETED
                        request.receipt_status = "INVOICE_PAID"
                        request.completed_at = now
                        NotificationService(ctx).enqueue(request.requester_id, request.id, "Invoice paid", "Owner menyetujui dan membayar invoice. Request selesai.")
                    else:
                        request.receipt_due_at = now + timedelta(hours=48)
                        request.receipt_status = "AWAITING_RECEIPT"
                        NotificationService(ctx).enqueue(ctx.owner_id, request.id, "Purchase paid — awaiting receipt", f"{request.party_name}: pembayaran berhasil, menunggu bukti pembelian.")
                        NotificationService(ctx).enqueue(request.requester_id, request.id, "Upload your receipt", "Pembayaran berhasil. Unggah receipt lewat History dalam 48 jam.")

                ctx.db.flush()

        except Declined as exc:
            row.status = "DECLINED"
            row.failure_reason = str(exc)
            row.processed_at = now_utc()

        except SQLAlchemyError:
            # Database error belum tentu berarti transaksi gagal.
            # Biarkan attempt durable diretry dengan key yang sama.
            ctx.db.rollback()
            raise

        except Exception:
            logger.exception(
                "Transaction processing error: %s", transaction_id
            )
            row.status = "FAILED"
            row.failure_reason = "Terjadi kesalahan teknis aplikasi."
            row.processed_at = now_utc()

        ctx.db.commit()
        return self.view(row)

    def list(
        self, pocket_id=None, card_id=None,
        status=None, offset=0, limit=50,
    ):
        if pocket_id:
            self.ctx.pocket(pocket_id)
        if card_id:
            self.ctx.card(card_id)

        from sqlalchemy import select, func
        from app.api.history.repository import HistoryRepository

        statement = HistoryRepository(self.ctx).query()
        if pocket_id:
            statement = statement.where(Transaction.pocket_id == pocket_id)
        if card_id:
            statement = statement.where(Transaction.card_id == card_id)
        if status:
            statement = statement.where(Transaction.status == status)
        total = self.ctx.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = self.ctx.db.scalars(statement.order_by(Transaction.created_at.desc(), Transaction.id)
                                   .offset(offset).limit(limit)).all()
        return {"total": total, "items": [self.view(row) for row in rows]}
