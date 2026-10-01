from uuid import UUID

from fastapi import APIRouter

from app.api.card.schema import CardCreate, CardStatusUpdate, CardUpdate
from app.api.card.service import CardService
from app.core.context import Ctx
from app.shared.enums import CardStatus

router = APIRouter(prefix="/cards", tags=["Card"])

@router.get("")
def list_cards(ctx: Ctx, pocket_id: UUID | None = None, status: CardStatus | None = None, category: str | None = None):
    return CardService(ctx).list(pocket_id, status, category)

@router.get("/limit-recommendation")
def recommend_limit(pocket_id: UUID, ctx: Ctx, category: str | None = None):
    return CardService(ctx).recommendation(pocket_id, category)

@router.post("", status_code=201)
def create_card(payload: CardCreate, ctx: Ctx):
    return CardService(ctx).create(payload)

@router.get("/{card_id}")
def detail_card(card_id: UUID, ctx: Ctx):
    return CardService(ctx).detail(card_id)

@router.patch("/{card_id}")
def update_card(card_id: UUID, payload: CardUpdate, ctx: Ctx):
    return CardService(ctx).update(card_id, payload)

@router.patch("/{card_id}/status")
def update_status(card_id: UUID, payload: CardStatusUpdate, ctx: Ctx):
    return CardService(ctx).set_status(card_id, payload)

@router.put("/{card_id}/employees/{employee_id}")
def grant_access(card_id: UUID, employee_id: UUID, ctx: Ctx):
    return CardService(ctx).access(card_id, employee_id, True)

@router.delete("/{card_id}/employees/{employee_id}")
def revoke_access(card_id: UUID, employee_id: UUID, ctx: Ctx):
    return CardService(ctx).access(card_id, employee_id, False)