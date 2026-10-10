from app.api.ai.insights import InsightCache
from app.api.ai.schema import AIAnalysis


def test_force_refresh_replaces_cache_and_coalesces_concurrent_requests():
    cache = InsightCache()
    key = ('owner', 'metrics')
    cache.store(key, AIAnalysis(available=True, summary='old'), 300)
    cached, claimed = cache.claim(key)
    assert not claimed and cached.summary == 'old'
    cached, claimed = cache.claim(key, force=True)
    assert claimed and cached is None
    pending, claimed = cache.claim(key, force=True)
    assert not claimed and pending.fallback_reason == 'LLM_GENERATION_IN_PROGRESS'
    cache.store(key, AIAnalysis(available=True, summary='new'), 300)
    cache.release(key)
    assert cache.lookup(key).summary == 'new'
