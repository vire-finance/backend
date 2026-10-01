import hashlib
import json
import logging

from decimal import Decimal

from sqlalchemy.exc import SQLAlchemyError

from app.api.auth.model import User
from app.api.notification.service import NotificationService
from app.api.payment.model import InvoicePayment
from app.api.transaction.model import Transaction
from app.api.transaction.repository import TransactionRepository
from app.shared.enums import CardStatus, FundRequestStatus, TransactionType
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
                "transaction_type", "status", "description",
                "failure_reason", "created_at", "processed_at",
            ),
            "is_simulated": True,
        }

    def execute(self, card_id, amount, description, key, request_id=None):
        ctx = self.ctx
        card = ctx.card(card_id)

        if request_id:
            ctx.owner_only()
            request = ctx.request(request_id)
            ensure(
                request.requester_id != ctx.user.id,
                "Tidak dapat approve Request sendiri.",
                403,
            )
            ensure(
                request.pocket_id == card.pocket_id,
                "Card harus berada di Pocket Request.",
            )

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
                    "description": description,
                    "type": kind.value,
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
                    transaction_type=kind,
                    status="PENDING",
                    description=description,
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

                    require(
                        request.requester_id != ctx.user.id,
                        "Self-approval tidak diperbolehkan.",
                    )
                    require(
                        request.status == FundRequestStatus.PENDING_APPROVAL,
                        "Request tidak lagi menunggu approval.",
                    )
                    require(
                        request.pocket_id == pocket.id
                        and request.total_amount == amount,
                        "Data Request tidak cocok dengan transaksi.",
                    )

                    requester = ctx.db.get(User, request.requester_id)
                    require(
                        requester is not None
                        and ctx.can_pocket(pocket, requester),
                        "Requester tidak lagi memiliki akses Pocket.",
                    )

                now = now_utc()
                local = local_time(now)

                require(
                    card.status == CardStatus.ACTIVE,
                    "Card frozen atau nonaktif.",
                )
                require(
                    (card.expiry_year, card.expiry_month)
                    >= (local.year, local.month),
                    "Card kedaluwarsa.",
                )
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

                pocket.remaining_amount -= Decimal(amount)
                card.balance -= Decimal(amount)

                # Kolom legacy; monthly usage tetap dihitung dari ledger.
                card.spent += Decimal(amount)

                row.status = "APPROVED"
                row.failure_reason = None
                row.processed_at = now

                if request:
                    request.status = FundRequestStatus.APPROVED
                    request.reviewed_by = ctx.user.id
                    request.reviewed_at = now

                    ctx.db.add(
                        InvoicePayment(
                            owner_id=ctx.owner_id,
                            transaction_id=row.id,
                            request_id=request.id,
                            status="QUEUED",
                        )
                    )

                    request.status = FundRequestStatus.COMPLETED
                    request.completed_at = now

                    NotificationService(ctx).enqueue(
                        request.requester_id,
                        request.id,
                        "Request disetujui",
                        "Transaksi simulasi berhasil. Invoice masuk "
                        "antrean pembayaran manual.",
                    )

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

        rows = self.repo.history(
            self.ctx.owner_id, pocket_id, card_id
        )

        visible = [
            row for row in rows
            if self.ctx.can_card(self.ctx.raw_card(row.card_id))
            and (status is None or row.status == status)
        ]

        return {
            "total": len(visible),
            "items": [
                self.view(row)
                for row in visible[offset:offset + limit]
            ],
        }