from sqlalchemy import func, select

from app.api.auth.model import User
from app.api.card.model import Card
from app.api.pocket.model import Pocket, PocketAccess
from app.shared.enums import UserRole
from app.shared.repository import Repository

class PocketRepository(Repository):
    model = Pocket

    def for_owner(self, owner_id):
        return self.db.scalars(
            select(Pocket)
            .where(Pocket.owner_id == owner_id)
            .order_by(Pocket.created_at, Pocket.id)
        ).all()

    def card_count(self, pocket_id):
        return self.db.scalar(
            select(func.count(Card.id)).where(Card.pocket_id == pocket_id)
        ) or 0

    def access(self, pocket_id, employee_id):
        return self.db.scalar(
            select(PocketAccess).where(
                PocketAccess.pocket_id == pocket_id,
                PocketAccess.employee_id == employee_id,
            )
        )

    def employees(self, owner_id, pocket_id=None):
        statement = select(User).where(
            User.employer_id == owner_id,
            User.role == UserRole.EMPLOYEE,
        )

        if pocket_id is not None:
            statement = statement.join(
                PocketAccess, PocketAccess.employee_id == User.id
            ).where(PocketAccess.pocket_id == pocket_id)

        return self.db.scalars(
            statement.order_by(User.username, User.id)
        ).all()