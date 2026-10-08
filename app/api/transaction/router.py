from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.transaction.schema import TransactionCreate
from app.api.transaction.service import TransactionService
from app.core.context import Ctx
from app.api.security.middleware import require_pin_verification
from app.api.security.tokens import ActionType

router = APIRouter(prefix="/transactions", tags=["Transaction"])
Key = Annotated[UUID, Header(alias="Idempotency-Key")]

def transaction_response(data):
    code = {
        "APPROVED": 200,
        "DECLINED": 409,
        "FAILED": 503,
        "PENDING": 202,
    }[data["status"]]

    return JSONResponse(
        status_code=code,
        content=jsonable_encoder(data),
    )

@router.post("", dependencies=[Depends(require_pin_verification(ActionType.PAYMENT))])
def create_transaction(payload: TransactionCreate, ctx: Ctx, key: Key):
    data = TransactionService(ctx).execute(
        payload.card_id,
        payload.amount,
        payload.description,
        key,
    )
    return transaction_response(data)

@router.get("")
def list_transactions(
    ctx: Ctx,
    pocket_id: UUID | None = None,
    card_id: UUID | None = None,
    status: str | None = Query(
        None, pattern="^(PENDING|APPROVED|DECLINED|FAILED)$"
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return TransactionService(ctx).list(
        pocket_id, card_id, status, offset, limit
    )