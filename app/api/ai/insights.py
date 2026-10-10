"""Explicit inference generation and bounded, owner-scoped in-process cache."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from threading import Lock
import time

from app.api.ai.analytics import AnalyticsService
from app.api.ai.provider import ProviderError, get_provider
from app.api.ai.schema import AIAnalysis, FinancialNarrative, OwnerInsightsResponse
from app.core.config import settings

logger = logging.getLogger(__name__)


class InsightCache:
    """Per-worker MVP cache; concurrent identical calls do not duplicate inference."""
    def __init__(self, capacity=256):
        self.capacity = capacity
        self.entries = OrderedDict()
        self.inflight = set()
        self.lock = Lock()

    def _get(self, key):
        entry = self.entries.get(key)
        if entry is not None:
            deadline, value = entry
            if deadline > time.monotonic():
                self.entries.move_to_end(key)
                return value.model_copy(deep=True, update={"cached": True})
            del self.entries[key]
        return None

    def lookup(self, key):
        with self.lock:
            return self._get(key)

    def claim(self, key, *, force=False):
        with self.lock:
            if force:
                if key in self.inflight:
                    return AIAnalysis(fallback_reason="LLM_GENERATION_IN_PROGRESS"), False
                self.entries.pop(key, None)
            cached = self._get(key)
            if cached is not None:
                return cached, False
            if key in self.inflight or len(self.inflight) >= self.capacity:
                return AIAnalysis(fallback_reason="LLM_GENERATION_IN_PROGRESS"), False
            self.inflight.add(key)
            return None, True

    def store(self, key, value, ttl):
        with self.lock:
            self.entries[key] = (time.monotonic() + ttl, value.model_copy(deep=True))
            self.entries.move_to_end(key)
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)

    def release(self, key):
        with self.lock:
            self.inflight.discard(key)


insight_cache = InsightCache()


def cache_key(owner_id, analytics):
    data = analytics.model_dump(mode="json")
    data["budget"].pop("as_of")
    # Changing model/provider/auth configuration also invalidates cached results.
    config = {name: getattr(settings, name) for name in
              ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_ENDPOINT", "LLM_MODEL", "LLM_API_KEY", "LLM_TIMEOUT")}
    digest = hashlib.sha256(json.dumps({"metrics": data, "config": config}, sort_keys=True,
                                      ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return str(owner_id), digest


def financial_evidence(analytics):
    data = analytics.model_dump(mode="json")
    # Aggregates only. No email, actor IDs, transaction descriptions, receipts or raw OCR.
    evidence = {key: data[key] for key in ("currency", "is_simulated", "period", "previous_period",
                "summary", "monthly_trend", "budget", "overall_status", "data_availability")}
    refs = {"summary", "budget", "data_availability"}
    for key, kind in (("pocket_breakdown", "pocket"), ("card_breakdown", "card")):
        evidence[key] = data[key][:10]
        refs.update(f"{kind}:{row['id']}" for row in evidence[key])
    evidence["category_breakdown"] = data["category_breakdown"][:10]
    refs.update(f"category:{row['category'] if row['category'] is not None else '<uncategorized>'}"
                for row in evidence["category_breakdown"])
    # Transaction outliers are already quantified rule evidence, not transaction history.
    evidence["signals"] = data["signals"][:20]
    for signal in evidence["signals"]:
        refs.add(f"signal:{signal['id']}")
        refs.update(signal["evidence_refs"])
    evidence["evidence_refs"] = sorted(refs)
    evidence["truncated"] = {key: len(data[key]) > len(evidence[key]) for key in
                            ("pocket_breakdown", "card_breakdown", "category_breakdown", "signals")}
    return evidence


def validated_narrative(value, evidence):
    # Revalidate even if a future provider returns a plain dictionary or model.
    narrative = FinancialNarrative.model_validate(value.model_dump() if isinstance(value, FinancialNarrative) else value)
    if narrative.overall_status.value != evidence["overall_status"]:
        raise ProviderError("LLM_UNSUPPORTED_EVIDENCE")
    refs = set(evidence["evidence_refs"])
    for insight in narrative.insights:
        if not set(insight.evidence_refs).issubset(refs):
            raise ProviderError("LLM_UNSUPPORTED_EVIDENCE")
    return narrative


class OwnerInsightService:
    def __init__(self, ctx):
        self.ctx = ctx

    def get(self, period=None, start_date=None, end_date=None, *, generate=False, force=False):
        analytics = AnalyticsService(self.ctx).analyze(period, start_date, end_date)
        key = cache_key(self.ctx.owner_id, analytics)
        if not generate:
            ai = insight_cache.lookup(key) or AIAnalysis(fallback_reason="NOT_GENERATED")
        else:
            ai, claimed = insight_cache.claim(key, force=force)
            if claimed:
                try:
                    evidence = financial_evidence(analytics)
                    provider = get_provider()
                    value = validated_narrative(provider.generate_financial_insight(evidence), evidence)
                    generated_at = datetime.now(timezone.utc)
                    ai = AIAnalysis(available=True, **value.model_dump(), generated_at=generated_at,
                        expires_at=generated_at + timedelta(seconds=settings.AI_INSIGHT_CACHE_TTL_SECONDS),
                        provider=provider.name, model=provider.model)
                except ProviderError as exc:
                    ai = AIAnalysis(fallback_reason=exc.code)
                    if exc.code != "LLM_NOT_CONFIGURED":
                        logger.warning("Owner interpretation unavailable (%s).", exc.code)
                except Exception as exc:
                    # Never log raw responses, prompts, credentials or exception strings.
                    logger.warning("Owner interpretation failed (%s).", type(exc).__name__)
                    ai = AIAnalysis(fallback_reason="LLM_INVALID_OUTPUT")
                finally:
                    try:
                        ttl = (settings.AI_INSIGHT_CACHE_TTL_SECONDS if ai.available
                               else settings.AI_INSIGHT_FAILURE_TTL_SECONDS)
                        insight_cache.store(key, ai, ttl)
                    finally:
                        insight_cache.release(key)
        return OwnerInsightsResponse(**analytics.model_dump(), ai_analysis=ai)
