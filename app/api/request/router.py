from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse

from app.api.request.schema import (
    DocumentAttach, FundRequestInput, RequestApprove,
    RequestReject, RequestSubmit, ReceiptSubmit, ReceiptReview,
)
from app.api.request.service import FundRequestService
from app.api.transaction.router import transaction_response
from app.core.context import Ctx
from app.api.security.middleware import require_pin_verification
from app.api.security.tokens import ActionType
from app.shared.enums import FundRequestStatus, FundRequestType

router = APIRouter(tags=["Fund Request"])
Key = Annotated[UUID, Header(alias="Idempotency-Key")]

@router.get("/fund-requests")
def list_requests(
    ctx: Ctx,
    request_status: FundRequestStatus | None = None,
    request_type: FundRequestType | None = None,
    search: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return FundRequestService(ctx).list(
        request_status, request_type, search, date_from, date_to, offset, limit,
    )

@router.post("/fund-requests", status_code=201)
def create_request(payload: FundRequestInput, ctx: Ctx):
    return FundRequestService(ctx).save(payload)

@router.get("/fund-requests/{request_id}")
def detail_request(request_id: UUID, ctx: Ctx):
    return FundRequestService(ctx).detail(request_id)

@router.put("/fund-requests/{request_id}")
def update_request(request_id: UUID, payload: FundRequestInput, ctx: Ctx,):
    return FundRequestService(ctx).save(payload, request_id)

@router.get("/documents/{document_id}/prefill")
def prefill(document_id: UUID, ctx: Ctx):
    return FundRequestService(ctx).prefill(document_id)

@router.post("/fund-requests/{request_id}/documents")
def attach(request_id: UUID, payload: DocumentAttach, ctx: Ctx):
    return FundRequestService(ctx).attach(
        request_id, payload.document_id
    )

@router.delete("/fund-requests/{request_id}/documents/{document_id}")
def detach(request_id: UUID, document_id: UUID, ctx: Ctx):
    return FundRequestService(ctx).attach(
        request_id, document_id, remove=True
    )

@router.get("/fund-requests/{request_id}/documents/{document_id}/file")
def document_file(request_id: UUID, document_id: UUID, ctx: Ctx, download: bool = False):
    path, mime, filename = FundRequestService(ctx).document_file(request_id, document_id)
    return FileResponse(
        path=str(path),
        media_type=mime,
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )

@router.post("/fund-requests/{request_id}/submit")
def submit(request_id: UUID, payload: RequestSubmit, ctx: Ctx):
    return FundRequestService(ctx).submit(request_id, payload)

@router.post("/fund-requests/{request_id}/approve", dependencies=[Depends(require_pin_verification(ActionType.APPROVAL, owner_only=True))])
def approve(request_id: UUID, payload: RequestApprove, ctx: Ctx, key: Key):
    result = FundRequestService(ctx).approve(request_id, payload, key)
    return transaction_response(result) if "transaction_type" in result else result

@router.post("/fund-requests/{request_id}/reject", dependencies=[Depends(require_pin_verification(ActionType.APPROVAL, owner_only=True))])
def reject(request_id: UUID, payload: RequestReject, ctx: Ctx):
    return FundRequestService(ctx).reject(request_id, payload)
@router.post("/fund-requests/{request_id}/receipt")
def submit_receipt(request_id: UUID, payload: ReceiptSubmit, ctx: Ctx):
    return FundRequestService(ctx).submit_receipt(request_id, payload)

@router.post("/fund-requests/{request_id}/receipt/review", dependencies=[Depends(require_pin_verification(ActionType.APPROVAL, owner_only=True))])
def review_receipt(request_id: UUID, payload: ReceiptReview, ctx: Ctx):
    return FundRequestService(ctx).review_receipt(request_id, payload)

@router.get("/fund-requests/{request_id}/receipt/file")
def receipt_file(request_id: UUID, ctx: Ctx):
    path, mime, filename = FundRequestService(ctx).receipt_file(request_id)
    return FileResponse(str(path), media_type=mime, filename=filename, content_disposition_type="inline", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
