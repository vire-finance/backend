"""Grounding/fault probes use deterministic evidence and mocked inference only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import json
from threading import Event, Semaphore
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.ai import insights, provider, service
from app.api.ai.insights import InsightCache, validated_narrative
from app.api.ai.provider import OllamaProvider, ProviderError, financial_output_schema
from app.api.ai.schema import AnalyticsResponse, FinancialNarrative
from app.core.config import Settings, settings
from app.shared.enums import FundRequestType, OCRStatus


def qualitative_narrative():
    return {"overall_status": "normal", "headline": "Ringkasan pengeluaran",
            "summary": "Tinjau pengeluaran Card terbesar secara manual.", "insights": [{
                "type": "general", "severity": "info", "title": "Pengeluaran Card",
                "description": "Riwayat terbatas memerlukan pemeriksaan manual.",
                "recommended_action": "Tinjau transaksi dan kebutuhan operasional.", "evidence_refs": ["summary"]}]}


def evidence():
    return {"overall_status": "normal", "signals": [], "evidence_refs": ["summary"],
            "summary": {"total_spending": 100}}


@pytest.mark.parametrize("field", ["headline", "summary", "title", "description", "recommended_action"])
@pytest.mark.parametrize("claim", ["Pengeluaran Rp999.999.999", "Turunkan ٥٠ persen", "Gunakan ９９％"])
def test_numerical_literals_are_rejected_in_every_free_text_field(field, claim):
    value = qualitative_narrative()
    target = value if field in {"headline", "summary"} else value["insights"][0]
    target[field] = claim
    with pytest.raises(ProviderError) as error:
        validated_narrative(value, evidence())
    assert error.value.code == "LLM_UNSUPPORTED_EVIDENCE"


def test_qualitative_narrative_accepted_and_private_schema_requests_qualitative_fields():
    value = qualitative_narrative()
    assert validated_narrative(value, evidence()).summary == value["summary"]
    output = financial_output_schema(evidence())
    for field in ("headline", "summary"):
        assert "0-9" in output["properties"][field]["pattern"]
    fields = output["$defs"]["FinancialInsight"]["properties"]
    assert all("0-9" in fields[field]["pattern"] for field in ("title", "description", "recommended_action"))


def analytics():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 11, 1, tzinfo=timezone.utc)
    return AnalyticsResponse(period={"start": start, "end": end, "label": "2026-10"},
        previous_period={"start": datetime(2026, 9, 1, tzinfo=timezone.utc), "end": start, "label": "2026-09"},
        summary={"total_spending": 100, "previous_period_spending": 0, "absolute_difference": 100,
                 "change_percentage": None, "comparison_status": "no_previous_spending", "transaction_count": 1,
                 "previous_transaction_count": 0, "average_transaction_amount": 100},
        monthly_trend=[{"period": "2026-10", "amount": 100, "transaction_count": 1}],
        pocket_breakdown=[], card_breakdown=[], category_breakdown=[],
        budget={"as_of": start, "allocated_amount": 500, "used_amount": 100, "remaining_amount": 400,
                "utilization_percentage": 20, "current_limit_period": "2026-10", "monthly_limit": 500,
                "current_month_spent": 100, "current_month_remaining_limit": 400,
                "current_month_limit_utilization_percentage": 20},
        top_spending={"pockets": [], "cards": [], "categories": [], "transactions": []},
        signals=[], overall_status="normal", data_availability={"approved_transactions_missing_processed_at": 0,
            "baseline_transaction_count": 0, "baseline_average_transaction_amount": None, "baseline_history_sufficient": False})


@pytest.mark.parametrize("bad_output", [False, True])
def test_owner_generation_validation_preserves_authoritative_metrics_and_model_metadata(monkeypatch, bad_output):
    snapshot = analytics()

    class Analytics:
        def __init__(self, ctx):
            pass

        def analyze(self, *args):
            return snapshot

    class FakeProvider:
        name, model = "ollama", "qwen3:1.7b"

        def generate_financial_insight(self, trusted_evidence):
            value = qualitative_narrative()
            if bad_output:
                value["summary"] = "Pengeluaran Rp999.999.999"
            return FinancialNarrative.model_validate(value)

    monkeypatch.setattr(insights, "AnalyticsService", Analytics)
    monkeypatch.setattr(insights, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(insights, "insight_cache", InsightCache())
    response = insights.OwnerInsightService(SimpleNamespace(owner_id=uuid4())).get(generate=True)
    assert response.model_dump(exclude={"ai_analysis"}) == snapshot.model_dump()
    if bad_output:
        assert not response.ai_analysis.available
        assert response.ai_analysis.fallback_reason == "LLM_UNSUPPORTED_EVIDENCE"
    else:
        assert response.ai_analysis.available and response.ai_analysis.model == "qwen3:1.7b"


def test_legacy_request_inference_receives_minimal_evidence_without_personal_text(monkeypatch):
    captured = {}
    row = SimpleNamespace(id=uuid4(), total_amount=Decimal(100), party_name="PRIVATE MERCHANT",
                          explanation="PRIVATE EMPLOYEE NOTE", request_type=FundRequestType.PURCHASE)
    document = SimpleNamespace(ocr_status=OCRStatus.COMPLETED, extracted_total_amount=Decimal(100),
        extracted_other_party_name="PRIVATE OCR MERCHANT", extracted_date=datetime(2026, 10, 1),
        raw_ocr_text="PRIVATE ACCOUNT OR RECEIPT TEXT")

    class Repository:
        def __init__(self, db):
            pass

        def comparison(self, item):
            return Decimal(100), 0

        def documents(self, request_id):
            return [document]

    class Requests:
        def __init__(self, ctx):
            pass

        def analysis(self, item):
            return "Manual review remains required."

    def narrative(context):
        captured.update(context)
        return "Periksa bukti dan aturan secara manual.", None

    monkeypatch.setattr(service, "FundRequestRepository", Repository)
    monkeypatch.setattr(service, "FundRequestService", Requests)
    monkeypatch.setattr(service, "narrative", narrative)
    ctx = SimpleNamespace(owner_only=lambda: None, request=lambda request_id: row, db=object())
    result = service.AIService(ctx).request_analysis(row.id)
    encoded = json.dumps(captured)
    assert "PRIVATE" not in encoded
    assert not {"party_name", "explanation", "text", "merchant", "date"}.intersection(captured)
    assert captured["documents"][0]["amount_matches_request"] is True
    assert captured["document_count"] == 1 and result["requires_manual_review"] is True


def test_inference_busy_fails_fast_and_capacity_recovers_after_request_finishes(monkeypatch):
    monkeypatch.setattr(provider, "_inference_slots", Semaphore(1))
    entered, finish = Event(), Event()

    def handle(request):
        entered.set()
        assert finish.wait(5)
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(qualitative_narrative())}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        runtime = OllamaProvider("http://localhost:11434/api/chat", "qwen3:1.7b", client=client)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(runtime.generate_financial_insight, evidence())
            try:
                assert entered.wait(5)
                with pytest.raises(ProviderError) as error:
                    runtime.generate_financial_insight(evidence())
                assert error.value.code == "LLM_BUSY"
            finally:
                finish.set()
            assert pending.result(timeout=5).summary == qualitative_narrative()["summary"]
        assert runtime.generate_financial_insight(evidence()).summary == qualitative_narrative()["summary"]


def test_legacy_summary_rejects_invented_numbers_and_releases_inference_capacity(monkeypatch):
    monkeypatch.setattr(provider, "_inference_slots", Semaphore(1))
    count = [0]

    def handle(request):
        count[0] += 1
        summary = "Pengeluaran Rp999999999" if count[0] == 1 else "Periksa bukti secara manual."
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"summary": summary})}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        runtime = OllamaProvider("http://localhost:11434/api/chat", "qwen3:1.7b", client=client)
        with pytest.raises(ProviderError):
            runtime.analyze_financial_evidence({"amount_idr": 100})
        assert runtime.analyze_financial_evidence({"amount_idr": 100}).summary == "Periksa bukti secara manual."


def test_ocr_stale_recovery_deadline_cannot_expire_before_whole_job_timeout():
    with pytest.raises(ValueError, match="OCR_STALE_JOB_SECONDS must exceed"):
        Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://local", SECRET_KEY="test",
                 OCR_JOB_TIMEOUT_SECONDS=180, OCR_STALE_JOB_SECONDS=180)
