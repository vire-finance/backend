import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.pocket.model import Pocket, PocketAccess
from app.api.pocket.repository import PocketRepository
from app.api.card.repository import CardRepository
from app.api.pocket.schema import PocketCreate, PocketUpdate,PocketBudgetUpdate, PocketListItem, PocketDetail

class PocketService:
    def __init__(self, db: Session):
        self.db = db
        self.pocket_repository = PocketRepository(db)
        self.card_repository = CardRepository(db)

    # Helper

    def _get_pocket(self, pocket_id: uuid.UUID) -> Pocket:
        pocket = (self.pocket_repository.get_by_id(pocket_id))
        if not pocket:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pocket not found."
            )
        return pocket

    def _ensure_owner(self, pocket: Pocket, user_id: uuid.UUID) -> None:
        if pocket.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Only the Pocket owner can perform this action."
                )
            )

    def _ensure_access(self, pocket: Pocket, user_id: uuid.UUID) -> None:
        # Owner selalu punya akses
        if pocket.owner_id == user_id:
            return

        # Employee harus terdaftar di PocketAccess
        has_access = (self.pocket_repository.has_access(pocket.id, user_id))

        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "You do not have access to this Pocket."
                )
            )

    # Pocket
    
    def list_pockets(self, user_id: uuid.UUID) -> list[PocketListItem]:
        rows = (self.pocket_repository.list_accessible(user_id))
        return [
            PocketListItem(
                id=pocket.id,
                owner_id=pocket.owner_id,
                name=pocket.name,
                allocated_amount=(pocket.allocated_amount),
                remaining_amount=(pocket.remaining_amount),
                theme=pocket.theme,
                card_count=card_count
            )
            for pocket, card_count in rows
        ]

    def create_pocket(self, owner_id: uuid.UUID, payload: PocketCreate) -> Pocket:
        pocket = Pocket(
            owner_id=owner_id,
            name=payload.name,
            allocated_amount=(payload.allocated_amount),
            # Ketika pocket baru dibuat, maka belum ada pengeluaran
            remaining_amount=(payload.allocated_amount),
            theme=payload.theme
        )
        self.pocket_repository.create(pocket)
        self.db.commit()
        self.db.refresh(pocket)
        return pocket

    def get_detail(self, pocket_id: uuid.UUID, user_id: uuid.UUID) -> PocketDetail:
        pocket = self._get_pocket(pocket_id)
        self._ensure_access(pocket, user_id)
        cards = self.card_repository.list_by_pocket(pocket.id)
        return PocketDetail(
            id=pocket.id,
            owner_id=pocket.owner_id,
            name=pocket.name,
            allocated_amount=(pocket.allocated_amount),
            remaining_amount=(pocket.remaining_amount),
            theme=pocket.theme,
            cards=cards
        )

    def update_pocket(self, pocket_id: uuid.UUID, owner_id: uuid.UUID, payload: PocketUpdate) -> Pocket:
        pocket = self._get_pocket(pocket_id)
        self._ensure_owner(pocket, owner_id)
        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(pocket, field, value)
        self.db.commit()
        self.db.refresh(pocket)
        return pocket

    def update_budget(self, pocket_id: uuid.UUID, owner_id: uuid.UUID, payload: PocketBudgetUpdate) -> Pocket:
        pocket = self._get_pocket(pocket_id)
        self._ensure_owner(pocket, owner_id)
        spent_amount = pocket.allocated_amount - pocket.remaining_amount
        new_budget = payload.allocated_amount
        # Tidak boleh menurunkan budget hingga di bawah uang yang sudah digunakan.
        if new_budget < spent_amount:
            raise HTTPException(
                status_code=(
                    status.HTTP_400_BAD_REQUEST
                ),
                detail=(
                    "Budget cannot be lower than the amount already spent."
                )
            )
        pocket.allocated_amount = new_budget
        pocket.remaining_amount = new_budget - spent_amount
        self.db.commit()
        self.db.refresh(pocket)
        return pocket

    # Pocket Access
    
    def grant_access(self, pocket_id: uuid.UUID, owner_id: uuid.UUID, employee_id: uuid.UUID) -> PocketAccess:
        pocket = self._get_pocket(pocket_id)
        self._ensure_owner(pocket, owner_id)
        # owner tidak perlu diberikan akses karena sudah memiliki akses penuh
        if employee_id == pocket.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pocket owner already has access to this Pocket."
            )
        existing_access = self.pocket_repository.get_access(pocket_id, employee_id)
        if existing_access:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Employee already has access to this Pocket."
            )
        access = PocketAccess(pocket_id=pocket_id, employee_id=employee_id)
        self.pocket_repository.grant_access(access)
        self.db.commit()
        self.db.refresh(access)
        return access

    def revoke_access(self, pocket_id: uuid.UUID, owner_id: uuid.UUID, employee_id: uuid.UUID) -> None:
        pocket = self._get_pocket(pocket_id)
        self._ensure_owner(pocket, owner_id)
        access = self.pocket_repository.get_access(pocket_id, employee_id)
        if not access:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee does not have access to this Pocket."
            )
        self.pocket_repository.revoke_access(access)
        self.db.commit()