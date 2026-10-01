from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth.dependencies import get_active_user
from app.api.auth.model import User
from app.api.card.model import Card, CardAccess
from app.api.pocket.model import Pocket, PocketAccess
from app.api.request.model import FundRequest
from app.core.database import get_db
from app.shared.enums import FundRequestStatus, UserRole
from app.shared.utils import ensure

class Context:
    def __init__(self, db, user):
        self.db = db
        self.user = user
        self.owner_id = (user.id if user.role == UserRole.OWNER else user.employer_id)

        ensure(
            self.owner_id is not None,
            "Employee belum terhubung ke Business.",
            403,
        )

        owner = db.get(User, self.owner_id)
        ensure(
            owner is not None and owner.role == UserRole.OWNER,
            "Business tidak tersedia.",
            403,
        )

    def lock(self):
        self.db.scalar(
            select(User).where(User.id == self.owner_id).with_for_update()
        )

    def owner_only(self):
        ensure(
            self.user.role == UserRole.OWNER,
            "Hanya Owner yang diizinkan.",
            403,
        )

    def employee_only(self):
        ensure(
            self.user.role == UserRole.EMPLOYEE,
            "Hanya Employee yang diizinkan.",
            403,
        )

    def employee(self, employee_id):
        row = self.db.get(User, employee_id)
        ensure(
            row is not None
            and row.role == UserRole.EMPLOYEE
            and row.employer_id == self.owner_id,
            "Employee tidak berada dalam Business ini.",
            404,
        )
        return row

    def raw_pocket(self, pocket_id):
        row = self.db.get(Pocket, pocket_id)
        ensure(
            row is not None and row.owner_id == self.owner_id,
            "Pocket tidak ditemukan.",
            404,
        )
        return row

    def can_pocket(self, pocket, user=None):
        user = user or self.user

        if user.role == UserRole.OWNER:
            return user.id == pocket.owner_id

        if user.employer_id != pocket.owner_id:
            return False

        return self.db.scalar(
            select(PocketAccess.id).where(
                PocketAccess.pocket_id == pocket.id,
                PocketAccess.employee_id == user.id,
            )
        ) is not None

    def pocket(self, pocket_id):
        row = self.raw_pocket(pocket_id)
        ensure(
            self.can_pocket(row),
            "Tidak memiliki akses Pocket.",
            403,
        )
        return row

    def raw_card(self, card_id):
        row = self.db.get(Card, card_id)
        ensure(row is not None, "Card tidak ditemukan.", 404)
        self.raw_pocket(row.pocket_id)
        return row

    def can_card(self, card, user=None):
        user = user or self.user
        pocket = self.raw_pocket(card.pocket_id)

        if self.can_pocket(pocket, user):
            return True

        if (
            user.role != UserRole.EMPLOYEE
            or user.employer_id != pocket.owner_id
        ):
            return False

        return self.db.scalar(
            select(CardAccess.id).where(
                CardAccess.card_id == card.id,
                CardAccess.employee_id == user.id,
            )
        ) is not None

    def card(self, card_id):
        row = self.raw_card(card_id)
        ensure(
            self.can_card(row),
            "Tidak memiliki akses Card.",
            403,
        )
        return row

    def request(self, request_id):
        row = self.db.get(FundRequest, request_id)
        ensure(row is not None, "Request tidak ditemukan.", 404)
        self.raw_pocket(row.pocket_id)

        allowed = row.requester_id == self.user.id or (
            self.user.role == UserRole.OWNER
            and row.status != FundRequestStatus.DRAFT
        )

        ensure(allowed, "Tidak memiliki akses Request.", 403)
        return row

    def commit(self, result):
        self.db.commit()
        return result


def get_context(request: Request, db: Session = Depends(get_db), user: User = Depends(get_active_user)):
    ctx = Context(db, user)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        ctx.lock()
    return ctx

Ctx = Annotated[Context, Depends(get_context)]