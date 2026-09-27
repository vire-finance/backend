import uuid

from fastapi import (
    APIRouter,
    Depends,
    Response,
    status
)
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth.dependencies import get_current_user

from app.api.card.service import CardService
from app.api.card.schema import CardCreate, CardUpdate, CardResponse, CardAccessCreate, CardAccessResponse

router = APIRouter(
    prefix="/pockets/{pocket_id}/cards",
    tags=["Card"]
)

def get_service(db: Session = Depends(get_db)):
    return CardService(db)

@router.get("", response_model=list[CardResponse])
def list_cards(pocket_id: uuid.UUID, current_user=Depends(get_current_user), service: CardService = Depends(get_service)):
    return service.list_cards(pocket_id=pocket_id, user_id=current_user.id)

@router.post("", response_model=CardResponse, status_code=status.HTTP_201_CREATED)
def create_card(pocket_id: uuid.UUID, payload: CardCreate, current_user=Depends(get_current_user), service: CardService = Depends(get_service)):
    return service.create_card(pocket_id=pocket_id, owner_id=current_user.id, payload=payload)

@router.patch("/{card_id}", response_model=CardResponse)
def update_card(pocket_id: uuid.UUID, card_id: uuid.UUID, payload: CardUpdate, current_user=Depends(get_current_user), service: CardService = Depends(get_service)):
    return service.update_card(
        pocket_id=pocket_id,
        card_id=card_id,
        owner_id=current_user.id,
        payload=payload
    )


@router.post("/{card_id}/access", response_model=CardAccessResponse, status_code=status.HTTP_201_CREATED)
def grant_card_access(pocket_id: uuid.UUID, card_id: uuid.UUID, payload: CardAccessCreate, current_user=Depends(get_current_user), service: CardService = Depends(get_service)):
    return service.grant_access(
        pocket_id=pocket_id,
        card_id=card_id,
        owner_id=current_user.id,
        employee_id=payload.employee_id
    )

@router.delete("/{card_id}/access/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_card_access(pocket_id: uuid.UUID, card_id: uuid.UUID, employee_id: uuid.UUID, current_user=Depends(get_current_user), service: CardService = Depends(get_service)):
    service.revoke_access(
        pocket_id=pocket_id,
        card_id=card_id,
        owner_id=current_user.id,
        employee_id=employee_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/{card_id}", response_model=CardResponse)
def get_card (pocket_id: uuid.UUID, card_id: uuid.UUID, current_user=Depends(get_current_user), service: CardService = Depends(get_service)):
    return service.get_card(
        pocket_id=pocket_id,
        card_id=card_id,
        user_id=current_user.id
    )