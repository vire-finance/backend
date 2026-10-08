from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from app.api.history.service import HistoryService
from app.core.context import Ctx

router = APIRouter(tags=["History"])


def history_filters(
    pocket_id: UUID | None = None, card_id: UUID | None = None,
    status: Literal["PENDING", "APPROVED", "DECLINED", "FAILED"] | None = None,
    search: str = Query("", max_length=200), category: str | None = Query(None, max_length=100),
    date_from: AwareDatetime | None = None, date_to: AwareDatetime | None = None,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
):
    return locals()


@router.get("/history")
def history(ctx: Ctx, filters: Annotated[dict, Depends(history_filters)]):
    return HistoryService(ctx).list(**filters)


@router.get("/history/{transaction_id}")
def history_detail(transaction_id: UUID, ctx: Ctx):
    return HistoryService(ctx).detail(transaction_id)


@router.get("/cards/{card_id}/history")
def card_history(card_id: UUID, ctx: Ctx, filters: Annotated[dict, Depends(history_filters)]):
    filters["card_id"] = card_id
    return HistoryService(ctx).list(**filters)
