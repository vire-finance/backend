import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.card.model import Card, CardAccess

class CardRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, card_id: uuid.UUID) -> Card | None:
        return self.db.scalar(
            select(Card).where(Card.id == card_id)
        )

    def list_by_pocket(self, pocket_id: uuid.UUID) -> list[Card]:
        return list(
            self.db.scalars(
                select(Card).where(Card.pocket_id == pocket_id).order_by(Card.created_at.desc())).all()
        )

    def create(self, card: Card) -> Card:
        self.db.add(card)
        self.db.flush()
        return card

    def get_access(self, card_id: uuid.UUID, employee_id: uuid.UUID) -> CardAccess | None:
        return self.db.scalar(
            select(CardAccess)
            .where(CardAccess.card_id == card_id, CardAccess.employee_id == employee_id
            )
        )

    def has_access(self, card_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        return self.get_access(card_id, employee_id) is not None

    def grant_access(self, access: CardAccess) -> CardAccess:
        self.db.add(access)
        self.db.flush()
        return access

    def revoke_access(self, access: CardAccess) -> None:
        self.db.delete(access)

    def list_accessible_by_user(self, pocket_id: uuid.UUID, user_id: uuid.UUID) -> list[Card]:
        return list(
            self.db.scalars(
                select(Card)
                .join(CardAccess, Card.id == CardAccess.card_id)
                .where(Card.pocket_id == pocket_id, CardAccess.employee_id == user_id)
                .order_by(Card.created_at.desc())
            ).all()
        )