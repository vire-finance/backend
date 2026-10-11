"""GroqCloud provider probes use mocked HTTP only; no live credentials.

These tests verify provider selection, request shape, valid and invalid
model output, API error mapping, timeouts, safe fallback behavior and
API-key hygiene. Live GroqCloud inference is intentionally not performed
here because no GroqCloud API key is available in this environment.
"""
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.ai import insights, provider
from app.api.ai.insights import InsightCache
from app.api.ai.provider import (
    GroqProvider, OllamaProvider, ProviderError, get_provider, provider_config,
)
from app.api.ai.schema import AnalyticsResponse
from app.core.config import settings
from datetime import datetime, timezone

TEST_KEY = "gsk_test_key_not_a_real_credential"
MODEL = "openai/gpt-oss-20b"


def narrative_payload():
    return {"overall_status": "normal", "headline": "Ringkasan pengeluaran",
            "summary": "Tinjau pengeluaran Card terbesar secara manual.",
            "insights": [{
                "type": "general", "severity": "info", "title": "Pengeluaran Card",
                "description": "Riwayat terbatas memerlukan pemeriksaan manual.",
                "recommended_action": "Tinjau transaksi dan kebutuhan operasional.",
                "evidence_refs": ["summary"]}]}


def evidence():
    return {"overall_status": "normal", "signals": [], "evidence_refs": ["summary"],
            "summary": {"total_spending": 100}}


def chat_response(content, finish_reason="stop"):
    return httpx.Response(200, json={"choices": [{"finish_reason": finish_reason,
                                                  "message": {"role": "assistant",
                                                              "content": content}}]})


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def groq_runtime(handler):
    return GroqProvider(provider.GROQ_CHAT_ENDPOINT, MODEL, TEST_KEY,
                        client=client_with(handler))


def configure(monkeypatch, **values):
    defaults = {"LLM_PROVIDER": "groq", "LLM_BASE_URL": "", "LLM_ENDPOINT": "",
                "LLM_MODEL": MODEL, "LLM_API_KEY": TEST_KEY}
    defaults.update(values)
    for name, value in defaults.items():
        monkeypatch.setattr(settings, name, value)


# --- provider selection ---

def test_groq_selects_default_endpoint_and_requires_api_key(monkeypatch):
    configure(monkeypatch, LLM_API_KEY="")
    with pytest.raises(ProviderError) as error:
        provider_config()
    assert error.value.code == "LLM_API_KEY_REQUIRED"
    configure(monkeypatch)
    assert provider_config() == ("groq", provider.GROQ_CHAT_ENDPOINT)


def test_groq_accepts_configured_https_endpoint(monkeypatch):
    endpoint = "https://ingest.example.com/openai/v1/chat/completions"
    configure(monkeypatch, LLM_BASE_URL=endpoint)
    assert provider_config() == ("groq", endpoint)


def test_groq_rejects_plain_http_endpoint(monkeypatch):
    configure(monkeypatch, LLM_BASE_URL="http://api.groq.com/openai/v1/chat/completions")
    with pytest.raises(ProviderError) as error:
        provider_config()
    assert error.value.code == "LLM_CONFIGURATION_REQUIRES_HTTPS"


def test_groq_rejects_endpoint_with_credentials_or_query(monkeypatch):
    configure(monkeypatch, LLM_BASE_URL="https://user:pass@api.groq.com/v1/chat/completions?x=1")
    with pytest.raises(ProviderError) as error:
        provider_config()
    assert error.value.code == "LLM_CONFIGURATION_INVALID"


def test_groq_requires_model(monkeypatch):
    configure(monkeypatch, LLM_MODEL="")
    with pytest.raises(ProviderError) as error:
        provider_config()
    assert error.value.code == "LLM_NOT_CONFIGURED"


def test_ollama_remains_the_local_provider(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER="ollama",
              LLM_BASE_URL="http://127.0.0.1:11434")
    selected, endpoint = provider_config()
    assert selected == "ollama"
    assert endpoint == "http://127.0.0.1:11434/api/chat"
    assert isinstance(get_provider(), OllamaProvider)


def test_legacy_endpoint_opt_in_still_selects_ollama(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER=None, LLM_BASE_URL="http://127.0.0.1:11434")
    selected, _ = provider_config()
    assert selected == "ollama"


def test_unknown_provider_is_rejected(monkeypatch):
    configure(monkeypatch, LLM_PROVIDER="azure")
    with pytest.raises(ProviderError) as error:
        provider_config()
    assert error.value.code == "LLM_PROVIDER_UNSUPPORTED"


def test_get_provider_constructs_groq_runtime(monkeypatch):
    configure(monkeypatch)
    runtime = get_provider()
    assert isinstance(runtime, GroqProvider)
    assert runtime.name == "groq"
    assert runtime.model == MODEL
    assert runtime.endpoint == provider.GROQ_CHAT_ENDPOINT


# --- valid and invalid model output ---

def test_valid_response_is_parsed_and_request_shape_is_correct():
    captured = {}

    def handle(request):
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return chat_response(json.dumps(narrative_payload()))

    runtime = groq_runtime(handle)
    result = runtime.generate_financial_insight(evidence())
    assert result.summary == narrative_payload()["summary"]
    assert captured["auth"] == "Bearer " + TEST_KEY
    assert captured["body"]["model"] == MODEL
    assert captured["body"]["stream"] is False
    assert captured["body"]["temperature"] == 0
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert [message["role"] for message in captured["body"]["messages"]] == ["system", "user"]
    # Only aggregate evidence is sent, never raw descriptions or receipts.
    assert json.loads(captured["body"]["messages"][1]["content"])["overall_status"] == "normal"


def test_invalid_json_is_rejected():
    runtime = groq_runtime(lambda request: chat_response("definitely not json"))
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(evidence())
    assert error.value.code == "LLM_INVALID_OUTPUT"


def test_schema_violating_numbers_are_rejected():
    payload = narrative_payload()
    payload["summary"] = "Pengeluaran Rp999.999.999"
    runtime = groq_runtime(lambda request: chat_response(json.dumps(payload)))
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(evidence())
    assert error.value.code == "LLM_INVALID_OUTPUT"


def test_thinking_text_is_rejected():
    payload = narrative_payload()
    payload["summary"] = "Selesai <tool_call>"
    runtime = groq_runtime(lambda request: chat_response(json.dumps(payload)))
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(evidence())
    assert error.value.code == "LLM_INVALID_OUTPUT"


def test_truncated_output_is_rejected():
    runtime = groq_runtime(
        lambda request: chat_response(json.dumps(narrative_payload()), finish_reason="length"))
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(evidence())
    assert error.value.code == "LLM_INVALID_OUTPUT"


def test_missing_content_is_rejected():
    response = httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {}}]})
    runtime = groq_runtime(lambda request: response)
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(evidence())
    assert error.value.code == "LLM_INVALID_OUTPUT"


def test_oversized_evidence_is_rejected_without_request():
    calls = []

    def handle(request):
        calls.append(request)
        return chat_response(json.dumps(narrative_payload()))

    runtime = groq_runtime(handle)
    huge = evidence()
    huge["bulky"] = "x" * 60_000
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(huge)
    assert error.value.code == "LLM_INPUT_TOO_LARGE"
    assert not calls


def test_oversized_response_is_rejected():
    huge = json.dumps({**narrative_payload(), "summary": "x" * 70_000})
    runtime = groq_runtime(lambda request: chat_response(huge))
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(evidence())
    assert error.value.code == "LLM_RESPONSE_TOO_LARGE"


# --- API errors, timeouts, rate limits ---

@pytest.mark.parametrize("status, code", [
    (429, "LLM_RATE_LIMIT"), (401, "LLM_AUTH_ERROR"), (403, "LLM_AUTH_ERROR"),
    (500, "LLM_UNAVAILABLE"), (503, "LLM_UNAVAILABLE"),
])
def test_http_errors_map_to_safe_fallback_codes(status, code):
    runtime = groq_runtime(lambda request: httpx.Response(status, json={"error": {"message": "boom"}}))
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(evidence())
    assert error.value.code == code


def test_network_timeout_maps_to_timeout():
    def handle(request):
        raise httpx.ConnectTimeout("connection timed out", request=request)

    runtime = groq_runtime(handle)
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(evidence())
    assert error.value.code == "LLM_TIMEOUT"


def test_transport_error_maps_to_unavailable():
    def handle(request):
        raise httpx.ConnectError("no route to host", request=request)

    runtime = groq_runtime(handle)
    with pytest.raises(ProviderError) as error:
        runtime.generate_financial_insight(evidence())
    assert error.value.code == "LLM_UNAVAILABLE"


# --- legacy fund-request narrative ---

def test_legacy_request_analysis_returns_qualitative_summary():
    payload = {"summary": "Periksa bukti dan aturan secara manual."}
    runtime = groq_runtime(lambda request: chat_response(json.dumps(payload)))
    result = runtime.analyze_financial_evidence({"amount_idr": 100})
    assert result.summary == "Periksa bukti dan aturan secara manual."


def test_legacy_summary_rejects_invented_numbers():
    payload = {"summary": "Pengeluaran Rp999999999"}
    runtime = groq_runtime(lambda request: chat_response(json.dumps(payload)))
    with pytest.raises(ProviderError) as error:
        runtime.analyze_financial_evidence({"amount_idr": 100})
    assert error.value.code == "LLM_UNSUPPORTED_EVIDENCE"


# --- AI Insights service fallback integration ---

def analytics_snapshot():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 11, 1, tzinfo=timezone.utc)
    return AnalyticsResponse(period={"start": start, "end": end, "label": "2026-10"},
        previous_period={"start": datetime(2026, 9, 1, tzinfo=timezone.utc), "end": start, "label": "2026-09"},
        summary={"total_spending": 100, "previous_period_spending": 0, "absolute_difference": 100,
                 "change_percentage": None, "comparison_status": "no_previous_spending",
                 "transaction_count": 1, "previous_transaction_count": 0,
                 "average_transaction_amount": 100},
        monthly_trend=[{"period": "2026-10", "amount": 100, "transaction_count": 1}],
        pocket_breakdown=[], card_breakdown=[], category_breakdown=[],
        budget={"as_of": start, "allocated_amount": 500, "used_amount": 100, "remaining_amount": 400,
                "utilization_percentage": 20, "current_limit_period": "2026-10", "monthly_limit": 500,
                "current_month_spent": 100, "current_month_remaining_limit": 400,
                "current_month_limit_utilization_percentage": 20},
        top_spending={"pockets": [], "cards": [], "categories": [], "transactions": []},
        signals=[], overall_status="normal",
        data_availability={"approved_transactions_missing_processed_at": 0, "baseline_transaction_count": 0,
                           "baseline_average_transaction_amount": None, "baseline_history_sufficient": False})


def test_owner_insights_fall_back_safely_when_groq_fails(monkeypatch):
    snapshot = analytics_snapshot()

    class Analytics:
        def __init__(self, ctx):
            pass

        def analyze(self, *args):
            return snapshot

    monkeypatch.setattr(insights, "AnalyticsService", Analytics)
    monkeypatch.setattr(insights, "get_provider",
                        lambda: groq_runtime(lambda request: httpx.Response(500)))
    monkeypatch.setattr(insights, "insight_cache", InsightCache())
    response = insights.OwnerInsightService(SimpleNamespace(owner_id=uuid4())).get(generate=True)
    assert response.model_dump(exclude={"ai_analysis"}) == snapshot.model_dump()
    assert not response.ai_analysis.available
    assert response.ai_analysis.fallback_reason == "LLM_UNAVAILABLE"
    assert response.ai_analysis.headline is None and response.ai_analysis.insights == []


def test_owner_insights_success_reports_groq_metadata(monkeypatch):
    snapshot = analytics_snapshot()

    class Analytics:
        def __init__(self, ctx):
            pass

        def analyze(self, *args):
            return snapshot

    monkeypatch.setattr(insights, "AnalyticsService", Analytics)
    monkeypatch.setattr(insights, "get_provider",
                        lambda: groq_runtime(
                            lambda request: chat_response(json.dumps(narrative_payload()))))
    monkeypatch.setattr(insights, "insight_cache", InsightCache())
    response = insights.OwnerInsightService(SimpleNamespace(owner_id=uuid4())).get(generate=True)
    assert response.ai_analysis.available
    assert response.ai_analysis.provider == "groq"
    assert response.ai_analysis.model == MODEL
    assert response.ai_analysis.summary == narrative_payload()["summary"]


# --- API key hygiene ---

def test_api_key_never_appears_in_results_or_error_codes():
    key = "gsk_secret_key_must_not_leak_anywhere"
    runtime = GroqProvider(provider.GROQ_CHAT_ENDPOINT, MODEL, key,
                           client=client_with(lambda request: chat_response(json.dumps(narrative_payload()))))
    result = runtime.generate_financial_insight(evidence())
    assert key not in json.dumps(result.model_dump())

    failing = GroqProvider(provider.GROQ_CHAT_ENDPOINT, MODEL, key,
                           client=client_with(lambda request: httpx.Response(401)))
    with pytest.raises(ProviderError) as error:
        failing.generate_financial_insight(evidence())
    assert key not in str(error.value.code)
    assert key not in repr(error.value)
