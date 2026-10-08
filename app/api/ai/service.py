from decimal import Decimal
from datetime import timezone

from sqlalchemy import func

from app.api.ai.provider import narrative
from app.api.dashboard.service import period_bounds
from app.api.history.repository import HistoryRepository
from app.api.request.repository import FundRequestRepository
from app.api.request.service import FundRequestService
from app.api.transaction.model import Transaction
from app.shared.enums import OCRStatus
from app.shared.utils import local_time, money, previous_month


class AIService:
    def __init__(self, ctx):
        self.ctx = ctx

    def request_analysis(self, request_id):
        self.ctx.owner_only()
        row = self.ctx.request(request_id)
        repo = FundRequestRepository(self.ctx.db)
        average, duplicates = repo.comparison(row)
        flags = []
        if average is not None and row.total_amount > Decimal(str(average)) * 2:
            flags.append("AMOUNT_ABOVE_TWICE_HISTORICAL_AVERAGE")
        if duplicates:
            flags.append("POSSIBLE_DUPLICATE")
        documents = repo.documents(row.id)
        evidence = []
        for doc in documents[:10]:
            if doc.ocr_status != OCRStatus.COMPLETED:
                flags.append("OCR_REQUIRES_MANUAL_REVIEW")
            if doc.extracted_total_amount is not None and doc.extracted_total_amount != row.total_amount:
                flags.append("OCR_AMOUNT_MISMATCH")
            evidence.append({"ocr_status": doc.ocr_status.value,
                "amount": str(doc.extracted_total_amount) if doc.extracted_total_amount is not None else None,
                "merchant": doc.extracted_other_party_name, "date": str(doc.extracted_date) if doc.extracted_date else None,
                "text": (doc.raw_ocr_text or "")[:2000]})
        context = {"amount_idr": money(row.total_amount), "party_name": row.party_name,
            "explanation": row.explanation[:5000], "request_type": row.request_type.value,
            "historical_average_idr": str(average) if average is not None else None,
            "matching_request_count": duplicates, "rule_flags": sorted(set(flags)), "documents": evidence}
        summary, fallback = narrative(context)
        heuristic = FundRequestService(self.ctx).analysis(row)
        if not documents:
            heuristic += " No document attached; document authenticity cannot be assessed."
        return {"request_id": row.id, "method": "LLM_WITH_RULES" if summary else "HEURISTIC",
            "verdict": "REVIEW_NEEDED" if flags else "NO_RULE_FLAGS",
            "summary": summary or heuristic, "rule_summary": heuristic,
            "flags": sorted(set(flags)), "fallback_reason": fallback,
            "requires_manual_review": True, "is_simulated": True}

    def anomalies(self, period=None, pocket_id=None):
        self.ctx.owner_only()
        if pocket_id:
            self.ctx.raw_pocket(pocket_id)
        start, end = period_bounds(period)
        baseline_start = local_time(start)
        for _ in range(3):
            baseline_start = previous_month(baseline_start)
        baseline_start = baseline_start.astimezone(timezone.utc).replace(tzinfo=None)
        repo = HistoryRepository(self.ctx)
        query = repo.query().where(Transaction.status == "APPROVED")
        if pocket_id:
            query = query.where(Transaction.pocket_id == pocket_id)
        baseline_query = query.where(Transaction.processed_at >= baseline_start, Transaction.processed_at < start)
        total, count = self.ctx.db.execute(baseline_query.with_only_columns(
            func.coalesce(func.sum(Transaction.amount), 0), func.count(Transaction.id))).one()
        current_query = query.where(Transaction.processed_at >= start, Transaction.processed_at < end)
        current = money(self.ctx.db.scalar(current_query.with_only_columns(func.coalesce(func.sum(Transaction.amount), 0))) or 0)
        monthly_average = money(total) / 3
        alerts = []
        if count >= 3:
            if current > monthly_average * 2:
                alerts.append({"code": "MONTHLY_SPENDING_SPIKE", "summary": "Spending exceeds twice the average of the previous three calendar months.", "amount_idr": current})
            average = Decimal(total) / count
            large = self.ctx.db.scalars(current_query.where(Transaction.amount > average * 2)
                .order_by(Transaction.amount.desc(), Transaction.id).limit(20)).all()
            alerts.extend({"code": "LARGE_TRANSACTION", "transaction_id": tx.id,
                "amount_idr": money(tx.amount), "summary": "Amount exceeds twice the historical average transaction."} for tx in large)
        return {"method": "RULE_BASED", "period": local_time(start).strftime("%Y-%m"),
            "baseline_transaction_count": count, "baseline_monthly_average": round(monthly_average, 2),
            "current_spending": current, "history_sufficient": count >= 3,
            "has_anomaly": bool(alerts), "alerts": alerts,
            "summary": "Unusual spending requires manual review." if alerts else (
                "No configured rule triggered; this does not guarantee the absence of fraud." if count >= 3
                else "Insufficient history for a reliable spending comparison."), "is_simulated": True}
