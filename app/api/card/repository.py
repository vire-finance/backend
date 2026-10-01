from sqlalchemy import select

from app.api.auth.model import User
from app.api.card.model import Card, CardAccess
from app.api.pocket.model import Pocket
from app.shared.enums import UserRole
from app.shared.repository import Repository

class CardRepository(Repository):
    model = Card

    def for_owner(self, owner_id, pocket_id=None):
        statement = (
            select(Card)
            .join(Pocket, Pocket.id == Card.pocket_id)
            .where(Pocket.owner_id == owner_id)
        )
        if pocket_id:
            statement = statement.where(Card.pocket_id == pocket_id)

        return self.db.scalars(
            statement.order_by(Card.created_at, Card.id)
        ).all()

    def access(self, card_id, employee_id):
        return self.db.scalar(
            select(CardAccess).where(
                CardAccess.card_id == card_id,
                CardAccess.employee_id == employee_id,
            )
        )

    def employees(self, owner_id):
        return self.db.scalars(
            select(User)
            .where(
                User.employer_id == owner_id,
                User.role == UserRole.EMPLOYEE,
            )
            .order_by(User.username, User.id)
        ).all()