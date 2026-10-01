from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response

from app.api.payment.schema import TopUpCreate
from app.api.payment.service import PaymentService
from app.core.context import Ctx
from app.shared.schema import Confirmation

router = APIRouter(tags=["Payment"])
Key = Annotated[UUID, Header(alias="Idempotency-Key")]

@router.get("/payments")
def list_payments(ctx: Ctx):
    return PaymentService(ctx).list()

@router.post("/payments", status_code=201)
def create_payment(payload: TopUpCreate, ctx: Ctx, key: Key):
    return PaymentService(ctx).create(payload, key)

@router.get("/payments/{payment_id}")
def get_payment(payment_id: UUID, ctx: Ctx):
    service = PaymentService(ctx)
    return service.view(service.get(payment_id))

@router.post("/payments/{payment_id}/confirm")
def confirm_payment(payment_id: UUID, payload: Confirmation, ctx: Ctx,):
    return PaymentService(ctx).confirm(payment_id)

@router.get("/payments/{payment_id}/qr")
def get_qr(payment_id: UUID, ctx: Ctx):
    return Response(
        content=PaymentService(ctx).qr(payment_id),
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, no-store"},
    )

@router.get("/invoice-payments")
def list_invoices(ctx: Ctx):
    return PaymentService(ctx).list_invoices()

@router.post("/invoice-payments/{payment_id}/confirm")
def confirm_invoice(payment_id: UUID, payload: Confirmation, ctx: Ctx,):
    return PaymentService(ctx).confirm_invoice(payment_id)