from collections import Counter
from datetime import timezone
from decimal import Decimal
from pathlib import Path

from app.api.auth.model import User
from app.api.notification.service import NotificationService
from app.api.request.model import FundRequest
from app.api.request.repository import FundRequestRepository
from app.api.transaction.service import TransactionService
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
                row, "id", "pocket_id", "explanation", "request_type",
                "party_name", "total_amount", "needed_by", "status",
                "rejection_reason", "reviewed_by", "reviewed_at",
                "completed_at", "created_at",
            ),
            "display_id": f"REQ-{row.id.hex[:8].upper()}",
            "requester": {
                "id": row.requester_id,
                "name": requester.username if requester else None,
            },
            "period_group": group,
        }

        if detail:
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
        self.ctx.pocket(payload.pocket_id)

        data = payload.model_dump()
        data["needed_by"] = payload.needed_by.astimezone(
            timezone.utc
        ).replace(tzinfo=None)

        if request_id:
            row = self.ctx.request(request_id)
            ensure(
                row.status == FundRequestStatus.DRAFT,
                "Hanya DRAFT yang dapat diubah.",
                409,
            )
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
            "explanation": (doc.raw_ocr_text or "")[:5000],
            "document_date": doc.extracted_date,
            "requires_user_review": True,
        }

    def attach(self, request_id, document_id, remove=False):
        self.ctx.employee_only()
        row = self.ctx.request(request_id)
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
        self.ctx.pocket(row.pocket_id)

        ensure(
            row.needed_by > now_utc(),
            "Tenggat sudah lewat. Perbarui Request.",
            409,
        )

        for doc in self.repo.documents(row.id):
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
            "Fund Request baru",
            f"{self.ctx.user.username} mengajukan {row.party_name}.",
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

        return TransactionService(self.ctx).execute(
            card_id=payload.card_id,
            amount=money(row.total_amount),
            description=f"Fund Request {row.id}: {row.party_name}"[:255],
            key=key,
            request_id=row.id,
        )

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
            "Request ditolak", payload.reason,
        )

        return self.ctx.commit(self.view(row, detail=True))