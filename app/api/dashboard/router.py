from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime

from app.api.dashboard.service import DashboardService
from app.api.history.router import history_filters
from app.api.history.service import HistoryService
from app.api.request.service import FundRequestService
from app.core.context import Ctx
from app.shared.enums import FundRequestStatus, FundRequestType

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("")
def dashboard(ctx: Ctx):
    return DashboardService(ctx).summary()


@router.get("/pending-requests")
def pending_requests(ctx: Ctx, search: str = Query("", max_length=200),
    request_type: FundRequestType | None = None, date_from: AwareDatetime | None = None,
    date_to: AwareDatetime | None = None, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=200)):
    return FundRequestService(ctx).list(FundRequestStatus.PENDING_APPROVAL, request_type,
                                       search, date_from, date_to, offset, limit)


@router.get("/transactions")
def transactions(ctx: Ctx, filters: Annotated[dict, Depends(history_filters)]):
    return HistoryService(ctx).list(**filters)


@router.get("/spending")
def spending(ctx: Ctx, period: str | None = Query(None, pattern=r"^[0-9]{4}-[0-9]{2}$"), pocket_id: UUID | None = None):
    return DashboardService(ctx).spending(period, pocket_id)


@router.get("/spending/export.csv")
def export(ctx: Ctx, period: str | None = Query(None, pattern=r"^[0-9]{4}-[0-9]{2}$"), pocket_id: UUID | None = None):
    return Response(DashboardService(ctx).export(period, pocket_id), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="vire-spending.csv"', "Cache-Control": "private, no-store"})
