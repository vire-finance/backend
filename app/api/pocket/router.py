import uuid

from fastapi import (
    APIRouter,
    Depends,
    Response,
    status
)
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth.dependencies import get_active_user
from app.api.pocket.service import PocketService
from app.api.pocket.schema import PocketCreate, PocketUpdate, PocketBudgetUpdate, PocketListItem, PocketDetail, PocketResponse, PocketAccessCreate, PocketAccessResponse

router = APIRouter(prefix="/pockets", tags=["Pocket"])

def get_service(db: Session = Depends(get_db)):
    return PocketService(db)

@router.get("", response_model=list[PocketListItem])
def list_pockets(
    current_user=Depends(get_active_user),
    service: PocketService = Depends(get_service)
):
    return service.list_pockets(current_user.id)

@router.post("", response_model=PocketResponse, status_code=status.HTTP_201_CREATED)
def create_pocket(
    payload: PocketCreate,
    current_user=Depends(get_active_user),
    service: PocketService = Depends(get_service)
):
    return service.create_pocket(owner_id=current_user.id, payload=payload)

@router.get("/{pocket_id}", response_model=PocketDetail)
def get_pocket(
    pocket_id: uuid.UUID,
    current_user=Depends(get_active_user),
    service: PocketService = Depends(get_service)
):
    return service.get_detail(pocket_id=pocket_id, user_id=current_user.id)

@router.patch("/{pocket_id}", response_model=PocketResponse)
def update_pocket(
    pocket_id: uuid.UUID,
    payload: PocketUpdate,
    current_user=Depends(get_active_user),
    service: PocketService = Depends(get_service)
):
    return service.update_pocket(
        pocket_id=pocket_id,
        owner_id=current_user.id,
        payload=payload
    )

@router.patch("/{pocket_id}/budget", response_model=PocketResponse)
def update_budget(
    pocket_id: uuid.UUID,
    payload: PocketBudgetUpdate,
    current_user=Depends(get_active_user),
    service: PocketService = Depends(get_service)
):
    return service.update_budget(
        pocket_id=pocket_id,
        owner_id=current_user.id,
        payload=payload
    )

@router.post("/{pocket_id}/access", response_model=PocketAccessResponse, status_code=status.HTTP_201_CREATED)
def grant_access(
    pocket_id: uuid.UUID,
    payload: PocketAccessCreate,
    current_user=Depends(get_active_user),
    service: PocketService = Depends(get_service)
):
    return service.grant_access(
        pocket_id=pocket_id,
        owner_id=current_user.id,
        employee_id=payload.employee_id
    )

@router.delete("/{pocket_id}/access/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_access(
    pocket_id: uuid.UUID,
    employee_id: uuid.UUID,
    current_user=Depends(get_active_user),
    service: PocketService = Depends(get_service)
):
    service.revoke_access(
        pocket_id=pocket_id,
        owner_id=current_user.id,
        employee_id=employee_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
