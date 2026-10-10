from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.ai.service import AIService
from app.api.ai.analytics import AnalyticsService
from app.api.ai.insights import OwnerInsightService
from app.api.ai.schema import AnalyticsResponse, OwnerInsightsResponse
from app.core.context import Ctx

router = APIRouter(prefix="/ai", tags=["AI Analysis"])


@router.get("/anomalies")
def anomalies(ctx: Ctx, period: str | None = Query(None, pattern=r"^[0-9]{4}-[0-9]{2}$"), pocket_id: UUID | None = None):
    return AIService(ctx).anomalies(period, pocket_id)


@router.get("/fund-requests/{request_id}/analysis")
def request_analysis(request_id: UUID, ctx: Ctx):
    return AIService(ctx).request_analysis(request_id)


@router.get("/owner/analytics", response_model=AnalyticsResponse)
def owner_analytics(ctx: Ctx, period: str | None = Query(None, max_length=32),
                    start_date: date | None = None, end_date: date | None = None):
    """Owner-scoped metrics/rules only; never calls inference."""
    return AnalyticsService(ctx).analyze(period, start_date, end_date)


@router.get("/owner/insights", response_model=OwnerInsightsResponse)
def owner_insights(ctx: Ctx, period: str | None = Query(None, max_length=32),
                   start_date: date | None = None, end_date: date | None = None):
    """Fresh analytics and cached AI interpretation; never calls inference."""
    return OwnerInsightService(ctx).get(period, start_date, end_date)


@router.post("/owner/insights/generate", response_model=OwnerInsightsResponse)
def generate_owner_insights(ctx: Ctx, period: str | None = Query(None, max_length=32),
                            start_date: date | None = None, end_date: date | None = None,
                            force: bool = False):
    """Explicit inference generation; matching evidence reuses a short-lived cache."""
    return OwnerInsightService(ctx).get(period, start_date, end_date, generate=True, force=force)
