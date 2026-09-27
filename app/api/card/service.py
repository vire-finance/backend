import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.card.model import Card, CardAccess

from app.api.card.repository import CardRepository
from app.api.pocket.repository import PocketRepository

from app.api.card.schema import CardCreate, CardUpdate

class CardService:
    def __init__(self, db: Session):
        self.db = db
        self.card_repository = CardRepository(db)
        self.pocket_repository = PocketRepository(db)

    def _get_pocket(self, pocket_id: uuid.UUID):
        pocket = self.pocket_repository.get_by_id(pocket_id)
        if not pocket:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pocket not found."
            )
        return pocket

    def _get_card(self, card_id: uuid.UUID) -> Card:
        card = self.card_repository.get_by_id(card_id)
        if not card:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Card not found."
            )
        return card

    def _ensure_owner(self, pocket, user_id: uuid.UUID) -> None:
        if pocket.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the Pocket owner can perform this action."
            )

    def create_card(self, pocket_id: uuid.UUID, owner_id: uuid.UUID, payload: CardCreate) -> Card:
        pocket = self._get_pocket(pocket_id)
        self._ensure_owner(pocket, owner_id)
        card = Card(
            pocket_id=pocket_id,
            name=payload.name,
            last_four_digits=payload.last_four_digits,
            network=payload.network,
            expiry_month=payload.expiry_month,
            expiry_year=payload.expiry_year,
            balance=payload.balance,
            theme=payload.theme
        )
        self.card_repository.create(card)
        self.db.commit()
        self.db.refresh(card)
        return card

    def list_cards(self, pocket_id: uuid.UUID, user_id: uuid.UUID) -> list[Card]:
        pocket = self._get_pocket(pocket_id)
        if pocket.owner_id == user_id:
            return self.card_repository.list_by_pocket(pocket_id)
        if self.pocket_repository.has_access(pocket_id, user_id):
            return self.card_repository.list_by_pocket(pocket_id)
        cards = self.card_repository.list_accessible_by_user(pocket_id, user_id)
        if not cards:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this Pocket."
            )
        return cards
    
    def update_card(self, pocket_id: uuid.UUID, card_id: uuid.UUID, owner_id: uuid.UUID, payload: CardUpdate) -> Card:
        pocket = self._get_pocket(pocket_id)
        self._ensure_owner(pocket, owner_id)
        card = self._get_card(card_id)
        if card.pocket_id != pocket_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Card not found in this Pocket."
            )
        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(card, field, value)
        self.db.commit()
        self.db.refresh(card)
        return card

    def grant_access(self, pocket_id: uuid.UUID, card_id: uuid.UUID, owner_id: uuid.UUID, employee_id: uuid.UUID) -> CardAccess:
        pocket = self._get_pocket(pocket_id)
        self._ensure_owner(pocket, owner_id)
        if self.pocket_repository.has_access(pocket_id, employee_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Employee already has access to this Pocket."
            )
        card = self._get_card(card_id)
        if card.pocket_id != pocket_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Card not found in this Pocket."
            )
        existing_access = (
            self.card_repository.get_access(
                card_id,
                employee_id
            )
        )
        if existing_access:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Employee already has access to this Card."
            )
        access = CardAccess(
            card_id=card_id,
            employee_id=employee_id
        )
        self.card_repository.grant_access(access)
        self.db.commit()
        self.db.refresh(access)
        return access

    def revoke_access(self, pocket_id: uuid.UUID, card_id: uuid.UUID, owner_id: uuid.UUID, employee_id: uuid.UUID) -> None:
        pocket = self._get_pocket(pocket_id)
        self._ensure_owner(pocket, owner_id)
        card = self._get_card(card_id)
        if card.pocket_id != pocket_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Card not found in this Pocket."
            )
        access = self.card_repository.get_access(card_id, employee_id)
        if not access:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Card access not found."
            )
        self.card_repository.revoke_access(access)
        self.db.commit()

    def _ensure_card_access(self, card: Card, user_id: uuid.UUID) -> None:
        pocket = self._get_pocket(card.pocket_id)
        # Owner Pocket
        if pocket.owner_id == user_id:
            return

        # Employee punya akses ke seluruh Pocket
        has_pocket_access = self.pocket_repository.has_access(pocket.id, user_id)

        if has_pocket_access:
            return

        # Employee punya akses langsung ke Card tertentu
        has_card_access = self.card_repository.has_access(card.id, user_id)

        if has_card_access:
            return

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this Card."
        )

    def get_card(self, pocket_id: uuid.UUID, card_id: uuid.UUID, user_id: uuid.UUID) -> Card:
        card = self._get_card(card_id)
        if card.pocket_id != pocket_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Card not found in this Pocket."
            )
        self._ensure_card_access(card, user_id)
        return card