from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header
from app.core.context import Ctx
from app.api.funding.schema import AllocationInput, FundAccountInput
from app.api.funding.service import FundingService
from app.api.security.middleware import require_pin_verification
from app.api.security.tokens import ActionType

router = APIRouter(prefix="/fund-account", tags=["Main Fund Account"])

@router.get("")
def account(ctx: Ctx):
    return FundingService(ctx).get()

@router.put("")
def save(payload: FundAccountInput, ctx: Ctx):
    return FundingService(ctx).save(payload)

@router.get("/movements")
def movements(ctx: Ctx):
    return FundingService(ctx).history()

@router.post("/allocations", dependencies=[Depends(require_pin_verification(ActionType.PAYMENT, owner_only=True))])
def allocate(payload: AllocationInput, ctx: Ctx, key: Annotated[UUID, Header(alias="Idempotency-Key")]):
    return FundingService(ctx).allocate(payload, key)
