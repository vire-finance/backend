import uuid

from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from app.api.pocket.model import Pocket, PocketAccess
from app.api.card.model import Card

class PocketRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, pocket_id: uuid.UUID) -> Pocket | None:
        return self.db.scalar(
            select(Pocket).where(Pocket.id == pocket_id)
        )

    def list_accessible(self, user_id: uuid.UUID):
        card_count = (
            select(func.count(Card.id))
            .where(Card.pocket_id == Pocket.id)
            .correlate(Pocket).scalar_subquery()
        )

        statement = (
            select(Pocket, card_count.label("card_count"))
            .outerjoin(PocketAccess, PocketAccess.pocket_id == Pocket.id)
            .where(
                or_(Pocket.owner_id == user_id, PocketAccess.employee_id == user_id)
            ).distinct()
        )

        return self.db.execute(statement).all()

    def create(self, pocket: Pocket) -> Pocket:
        self.db.add(pocket)
        self.db.flush()
        return pocket

    def get_access(self, pocket_id: uuid.UUID, employee_id: uuid.UUID) -> PocketAccess | None:
        return self.db.scalar(
            select(PocketAccess)
            .where(
                PocketAccess.pocket_id == pocket_id,
                PocketAccess.employee_id == employee_id
            )
        )

    def has_access(self, pocket_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        return self.get_access(pocket_id, employee_id) is not None

    def grant_access(self, access: PocketAccess) -> PocketAccess:
        self.db.add(access)
        self.db.flush()
        return access

    def revoke_access(self, access: PocketAccess) -> None:
        self.db.delete(access)