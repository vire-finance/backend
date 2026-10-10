from collections import Counter
from datetime import timezone
from decimal import Decimal
from pathlib import Path

from app.api.auth.model import User
from app.api.notification.service import NotificationService
from app.api.request.model import FundRequest
from app.api.request.repository import FundRequestRepository
from sqlalchemy import select
from app.api.transaction.model import Transaction
from app.api.ocr.model import OCRDocument
from app.core.config import settings
from app.shared.enums import FundRequestStatus, OCRStatus
from app.shared.schema import MAX_MONEY
from app.shared.utils import ensure, fields, local_time, money, now_utc, previous_month

class FundRequestService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.repo = FundRequestRepository(ctx.db)

    def analysis(self, row):
        average, duplicates = self.repo.comparison(row)
        notes = []

        if average is None:
            notes.append("Histori belum cukup untuk membandingkan nominal.")
        elif row.total_amount > Decimal(str(average)) * 2:
            notes.append(
                "Nominal lebih dari dua kali rata-rata request selesai."
            )
        else:
            notes.append(
                "Nominal tidak melebihi dua kali rata-rata request selesai."
            )

        if duplicates:
            notes.append(
                f"Ada {duplicates} request lain dengan merchant dan nominal "
                "sama. Periksa kemungkinan duplikasi."
            )

        for doc in self.repo.documents(row.id):
            if doc.ocr_status == OCRStatus.FAILED:
                notes.append("Ada OCR gagal; review manual diperlukan.")
            if (
                doc.extracted_total_amount is not None
                and doc.extracted_total_amount != row.total_amount
            ):
                notes.append("Nominal input berbeda dari hasil OCR.")

        notes.append(
            "Analisis heuristik ini bukan kepastian kewajaran atau fraud."
        )
        return " ".join(notes)

    def view(self, row, detail=False):
        requester = self.ctx.db.get(User, row.requester_id)
        created = local_time(row.created_at)
        now = local_time(now_utc())
        last_month = previous_month(now.replace(day=1))

        if created.date() == now.date():
            group = "Today"
        elif (created.year, created.month) == (
            last_month.year, last_month.month
        ):
            group = "Last Month"
        else:
            group = created.strftime("%Y-%m")

        result = {
            **fields(
                row, "id", "pocket_id", "explanation", "request_type", "category",
                "party_name", "total_amount", "needed_by", "status",
                "rejection_reason", "reviewed_by", "reviewed_at",
                "completed_at", "created_at", "card_id", "payment_method",
                "recipient_account", "payment_executor", "receipt_status", "paid_at", "receipt_due_at",
                "receipt_note", "receipt_review_reason",
            ),
            "display_id": f"REQ-{row.id.hex[:8].upper()}",
            "requester": {
                "id": row.requester_id,
                "name": (requester.full_name or requester.username) if requester else None,
                "avatar_url": requester.avatar_url if requester else None,
            },
            "period_group": group,
        }

        card = self.ctx.raw_card(row.card_id) if row.card_id else None
        result["card"] = {"id": card.id, "name": card.name, "last_four_digits": card.last_four_digits} if card else None
        result["workflow_status"] = self.workflow_status(row)
        transaction = self.ctx.db.scalar(select(Transaction).where(Transaction.fund_request_id == row.id, Transaction.status == "APPROVED"))
        result["transaction_id"] = transaction.id if transaction else None
        if detail:
            receipt = self.ctx.db.get(OCRDocument, row.receipt_document_id) if row.receipt_document_id else None
            result["receipt"] = ({"id": receipt.id, "file_name": receipt.original_filename,
                "download_url": f"/fund-requests/{row.id}/receipt/file"} if receipt else None)
            result["ai_analysis"] = {
                "method": "HEURISTIC",
                "summary": self.analysis(row),
            }
            result["documents"] = [
                {
                    **fields(
                        doc, "id", "original_filename",
                        "mime_type", "ocr_status", "created_at",
                    ),
                    "preview_url": (
                        f"/fund-requests/{row.id}/documents/{doc.id}/file"
                    ),
                    "download_url": (
                        f"/fund-requests/{row.id}/documents/{doc.id}/file"
                        "?download=true"
                    ),
                }
                for doc in self.repo.documents(row.id)
            ]

        return result

    def list(
        self, status=None, request_type=None, search="",
        date_from=None, date_to=None, offset=0, limit=50,
    ):
        rows = self.repo.visible(self.ctx.owner_id, self.ctx.user)
        counts = Counter(row.status.value for row in rows)

        def normalize(value):
            if value is None:
                return None
            ensure(
                value.tzinfo is not None,
                "Filter waktu harus menyertakan timezone.",
            )
            return value.astimezone(timezone.utc).replace(tzinfo=None)

        date_from = normalize(date_from)
        date_to = normalize(date_to)

        if date_from and date_to:
            ensure(date_from < date_to, "Rentang tanggal tidak valid.")

        search = search.strip().lower()
        filtered = [
            row for row in rows
            if (status is None or row.status == status)
            and (request_type is None or row.request_type == request_type)
            and (
                not search
                or search in (
                    f"{row.party_name} {row.explanation} "
                    f"{row.id} REQ-{row.id.hex[:8]}"
                ).lower()
            )
            and (date_from is None or row.created_at >= date_from)
            and (date_to is None or row.created_at < date_to)
        ]

        return {
            "summary": {
                "draft": counts["DRAFT"],
                "waiting": counts["PENDING_APPROVAL"],
                "rejected": counts["REJECTED"],
                "accepted": counts["APPROVED"] + counts["COMPLETED"],
            },
            "total": len(filtered),
            "items": [
                self.view(row)
                for row in filtered[offset:offset + limit]
            ],
        }

    def detail(self, request_id):
        return self.view(self.ctx.request(request_id), detail=True)

    def save(self, payload, request_id=None):
        self.ctx.employee_only()
        ensure(payload.payment_executor == "OWNER_PAYMENT", "Request invoice harus dibayar owner. Pembayaran langsung employee dilakukan melalui Payment.", 409)
        if payload.card_id:
            card = self.ctx.card(payload.card_id)
            ensure(card.pocket_id == payload.pocket_id, "Kartu tidak berada di pocket ini.")
            if payload.payment_executor == "EMPLOYEE_PAYMENT":
                ensure(payload.category in card.allowed_categories, "Kategori tidak diizinkan kartu ini.")
                pocket = self.ctx.raw_pocket(card.pocket_id)
                from app.api.transaction.repository import TransactionRepository
                usage = TransactionRepository(self.ctx.db)
                ensure(card.balance >= payload.total_amount and pocket.remaining_amount >= payload.total_amount
                       and usage.spent(card_id=card.id) + payload.total_amount <= card.monthly_limit
                       and usage.spent(pocket_id=pocket.id) + payload.total_amount <= pocket.monthly_limit,
                       "Budget/monthly limit tidak cukup. Gunakan Owner Payment Request.", 409)
        else:
            self.ctx.pocket(payload.pocket_id)
        ensure(payload.payment_method == "QRIS" or payload.recipient_account, "Rekening/nomor tujuan transfer wajib diisi.")

        data = payload.model_dump()
        data["needed_by"] = payload.needed_by.astimezone(
            timezone.utc
        ).replace(tzinfo=None)

        if request_id:
            row = self.ctx.request(request_id)
            ensure(
                row.status == FundRequestStatus.DRAFT or (
                    row.payment_executor == "EMPLOYEE_PAYMENT" and row.paid_at is None
                    and row.status in (FundRequestStatus.PENDING_APPROVAL, FundRequestStatus.APPROVED)
                ),
                "Hanya DRAFT atau request lama yang belum dibayar dapat diubah.",
                409,
            )
            row.status = FundRequestStatus.DRAFT
            row.receipt_status = None
            row.reviewed_by = None
            row.reviewed_at = None
            for name, value in data.items():
                setattr(row, name, value)
            self.ctx.db.flush()
        else:
            row = self.repo.add(
                FundRequest(
                    **data,
                    requester_id=self.ctx.user.id,
                    status=FundRequestStatus.DRAFT,
                )
            )

        return self.ctx.commit(self.view(row, detail=True))

    def prefill(self, document_id):
        self.ctx.employee_only()
        doc = self.repo.document(document_id)
        ensure(
            doc is not None and doc.user_id == self.ctx.user.id,
            "Dokumen tidak ditemukan.",
            404,
        )
        ensure(
            doc.ocr_status == OCRStatus.COMPLETED,
            "OCR belum selesai atau gagal.",
            409,
        )

        amount = doc.extracted_total_amount
        usable = (
            amount is not None
            and amount == amount.to_integral_value()
            and 0 < amount <= MAX_MONEY
        )

        return {
            "document_id": doc.id,
            "party_name": doc.extracted_other_party_name,
            "total_amount": int(amount) if usable else None,
            "extracted_total_amount": str(amount) if amount is not None else None,
            "explanation": None,
            "document_date": doc.extracted_date,
            "requires_user_review": True,
        }

    def attach(self, request_id, document_id, remove=False):
        self.ctx.employee_only()
        row = self.ctx.request(request_id)
        if row.card_id:
            self.ctx.card(row.card_id)
        else:
            self.ctx.pocket(row.pocket_id)

        ensure(
            row.status == FundRequestStatus.DRAFT,
            "Lampiran hanya dapat diubah saat DRAFT.",
            409,
        )

        doc = self.repo.document(document_id, lock=True)
        ensure(
            doc is not None and doc.user_id == self.ctx.user.id,
            "Dokumen tidak ditemukan.",
            404,
        )

        if remove:
            ensure(
                doc.fund_request_id == row.id,
                "Dokumen bukan lampiran Request ini.",
                404,
            )
            doc.fund_request_id = None
        else:
            ensure(
                doc.fund_request_id in (None, row.id),
                "Dokumen sudah digunakan Request lain.",
                409,
            )
            doc.fund_request_id = row.id

        self.ctx.db.flush()
        return self.ctx.commit(self.view(row, detail=True))

    def document_file(self, request_id, document_id):
        row = self.ctx.request(request_id)
        doc = self.repo.document(document_id)

        ensure(
            doc is not None and doc.fund_request_id == row.id,
            "Dokumen tidak ditemukan.",
            404,
        )

        root = Path(settings.UPLOAD_DIR).resolve()
        path = Path(doc.storage_path).resolve()

        ensure(
            path.is_relative_to(root) and path.is_file(),
            "File tidak tersedia.",
            404,
        )

        return path, doc.mime_type, Path(doc.original_filename).name

    def submit(self, request_id, payload):
        self.ctx.employee_only()
        row = self.ctx.request(request_id)

        if row.status == FundRequestStatus.PENDING_APPROVAL:
            return self.view(row, detail=True)

        ensure(
            row.status == FundRequestStatus.DRAFT,
            "Hanya DRAFT yang dapat disubmit.",
            409,
        )
        if row.card_id:
            self.ctx.card(row.card_id)
        else:
            self.ctx.pocket(row.pocket_id)

        ensure(
            row.needed_by > now_utc(),
            "Tenggat sudah lewat. Perbarui Request.",
            409,
        )

        docs = self.repo.documents(row.id)
        if row.payment_executor == "OWNER_PAYMENT":
            ensure(bool(docs), "Invoice wajib dilampirkan sebelum Owner Payment Request dikirim.", 409)
            ensure(bool(row.recipient_account), "Alamat pembayaran merchant wajib diisi.", 409)
        for doc in ([] if row.payment_executor == "OWNER_PAYMENT" else docs):
            ensure(
                doc.ocr_status not in (
                    OCRStatus.PENDING, OCRStatus.PROCESSING,
                ),
                "OCR masih berjalan. Tunggu sebelum submit.",
                409,
            )
            ensure(
                doc.ocr_status == OCRStatus.COMPLETED
                or (
                    doc.ocr_status == OCRStatus.FAILED
                    and payload.allow_failed_ocr
                ),
                "OCR gagal. Koreksi manual dan gunakan "
                "allow_failed_ocr=true, atau ganti dokumen.",
                409,
            )

        row.status = FundRequestStatus.PENDING_APPROVAL
        row.ai_analysis = self.analysis(row)

        NotificationService(self.ctx).enqueue(
            self.ctx.owner_id, row.id,
            f"Request Payment from {self.ctx.user.full_name or self.ctx.user.username}",
            f"Please review {self.ctx.user.full_name or self.ctx.user.username}'s payment request on "
            f"{self.ctx.raw_card(row.card_id).name if row.card_id else self.ctx.raw_pocket(row.pocket_id).name} Card.",
        )

        return self.ctx.commit(self.view(row, detail=True))

    def approve(self, request_id, payload, key):
        self.ctx.owner_only()
        row = self.ctx.request(request_id)

        ensure(
            row.requester_id != self.ctx.user.id,
            "Tidak dapat approve Request sendiri.",
            403,
        )

        card = self.ctx.card(payload.card_id)
        ensure(row.card_id in (None, card.id) and card.pocket_id == row.pocket_id, "Kartu berbeda dari pengajuan.", 409)
        ensure(self.ctx.can_card(card, self.ctx.employee(row.requester_id)), "Employee tidak memiliki akses kartu.", 403)
        if row.payment_executor == "OWNER_PAYMENT":
            ensure(bool(self.repo.documents(row.id)), "Invoice wajib tersedia sebelum pembayaran.", 409)
            ensure(bool(row.recipient_account), "Alamat pembayaran merchant wajib tersedia.", 409)
            from app.api.transaction.service import TransactionService
            return TransactionService(self.ctx).execute(card.id, money(row.total_amount), row.explanation[:255], key, request_id=row.id, category=row.category, payment_method=row.payment_method, recipient_account=row.recipient_account)
        ensure(False, "Request lama harus dilengkapi invoice dan dikirim ulang oleh employee sebelum owner membayar.", 409)

    def reject(self, request_id, payload):
        self.ctx.owner_only()
        row = self.ctx.request(request_id)

        ensure(
            row.requester_id != self.ctx.user.id,
            "Tidak dapat reject Request sendiri.",
            403,
        )
        ensure(
            row.status == FundRequestStatus.PENDING_APPROVAL,
            "Request tidak sedang menunggu approval.",
            409,
        )

        row.status = FundRequestStatus.REJECTED
        row.rejection_reason = payload.reason
        row.reviewed_by = self.ctx.user.id
        row.reviewed_at = now_utc()

        NotificationService(self.ctx).enqueue(
            row.requester_id, row.id,
            "Your Request Was Rejected", "Please check the comment given to your request.",
        )

        return self.ctx.commit(self.view(row, detail=True))
    @staticmethod
    def workflow_status(row):
        if row.receipt_status in ("AWAITING_RECEIPT", "NEEDS_CLARIFICATION") and row.receipt_due_at and row.receipt_due_at < now_utc():
            return "RECEIPT_OVERDUE"
        return row.receipt_status or row.status.value

    def submit_receipt(self, request_id, payload):
        self.ctx.employee_only()
        row = self.ctx.request(request_id)
        ensure(row.paid_at is not None and row.status == FundRequestStatus.APPROVED, "Request belum dibayar atau sudah closed.", 409)
        ensure(row.receipt_status in ("AWAITING_RECEIPT", "NEEDS_CLARIFICATION", "RECEIPT_SUBMITTED"), "Receipt tidak dapat diubah.", 409)
        doc = self.repo.document(payload.document_id, lock=True)
        ensure(doc is not None and doc.user_id == self.ctx.user.id, "Dokumen tidak ditemukan.", 404)
        ensure(doc.fund_request_id in (None, row.id), "Dokumen sudah dipakai request lain.", 409)
        ensure(not self.ctx.db.scalar(select(FundRequest.id).where(FundRequest.receipt_document_id == doc.id, FundRequest.id != row.id)), "Receipt sudah digunakan request lain.", 409)
        if row.receipt_document_id == doc.id and row.receipt_status == "RECEIPT_SUBMITTED" and row.receipt_note == payload.note:
            return self.view(row, detail=True)
        doc.fund_request_id = row.id
        row.receipt_document_id = doc.id
        row.receipt_note = payload.note
        row.receipt_status = "RECEIPT_SUBMITTED"
        NotificationService(self.ctx).enqueue(self.ctx.owner_id, row.id, "Receipt ready for review", f"{self.ctx.user.username} mengunggah bukti untuk {row.party_name}.")
        return self.ctx.commit(self.view(row, detail=True))

    def review_receipt(self, request_id, payload):
        self.ctx.owner_only()
        row = self.ctx.request(request_id)
        ensure(row.status == FundRequestStatus.APPROVED and row.receipt_status == "RECEIPT_SUBMITTED" and row.receipt_document_id, "Receipt belum tersedia untuk review.", 409)
        ensure(payload.decision != "NEEDS_CLARIFICATION" or payload.reason.strip(), "Alasan klarifikasi wajib diisi.")
        row.receipt_status = payload.decision
        row.receipt_review_reason = payload.reason
        if payload.decision == "VERIFIED":
            row.status = FundRequestStatus.COMPLETED
            row.completed_at = now_utc()
        else:
            from datetime import timedelta
            row.receipt_due_at = now_utc() + timedelta(hours=48)
            row.receipt_overdue_notified = False
        NotificationService(self.ctx).enqueue(row.requester_id, row.id, "Receipt review", payload.reason or "Receipt verified. Purchase Request closed.")
        return self.ctx.commit(self.view(row, detail=True))

    def receipt_file(self, request_id):
        row = self.ctx.request(request_id)
        ensure(row.receipt_document_id is not None, "Receipt belum tersedia.", 404)
        return self.document_file(request_id, row.receipt_document_id)
