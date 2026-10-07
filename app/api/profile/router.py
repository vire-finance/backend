from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth.dependencies import get_active_user as get_current_user
from app.api.auth.model import User
from app.api.pocket.model import Pocket
from app.api.profile.schema import EmployeeResponse, ProfileResponse, ProfileUpdate, WorkspaceUpdate
from app.core.database import get_db
from app.shared.enums import UserRole

router = APIRouter(prefix="/api/v1", tags=["Profile"])


@router.get("/profile", response_model=ProfileResponse)
def get_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    pockets = db.scalars(
        select(Pocket)
        .where(Pocket.owner_id == current_user.id)
        .order_by(Pocket.created_at.desc())
    ).all()
    return ProfileResponse(
        id=current_user.id,
        avatar_url=current_user.avatar_url,
        full_name=current_user.full_name or current_user.username,
        role=(
            "BUSINESS_OWNER"
            if current_user.role == UserRole.OWNER
            else "EMPLOYEE"
        ),
        linked_fund_accounts=pockets,
    )


@router.get("/workspace/employees", response_model=list[EmployeeResponse])
def list_workspace_employees(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[EmployeeResponse]:
    if current_user.role != UserRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only business owners can access workspace employees.",
        )
    employees = db.scalars(
        select(User)
        .where(
            User.employer_id == current_user.id,
            User.role == UserRole.EMPLOYEE,
        )
        .order_by(User.created_at.desc())
    ).all()
    return [
        EmployeeResponse(
            id=employee.id,
            avatar_url=employee.avatar_url,
            full_name=employee.full_name or employee.username,
            role="EMPLOYEE",
        )
        for employee in employees
    ]


@router.patch("/profile", response_model=ProfileResponse)
def update_profile(payload: ProfileUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    updates = payload.model_dump(exclude_unset=True)
    if "full_name" in updates and updates["full_name"] is None:
        raise HTTPException(status_code=422, detail="Full name cannot be null.")
    for key, value in updates.items():
        setattr(current_user, key, str(value) if value is not None else None)
    db.commit()
    return get_profile(current_user, db)


def _workspace(current_user, db):
    if current_user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Only owners can manage workspace settings.")
    count = db.scalar(select(func.count(User.id)).where(User.employer_id == current_user.id, User.role == UserRole.EMPLOYEE)) or 0
    return {"owner_id": current_user.id, "fund_account_type": current_user.fund_account_type,
        "employees_managed": current_user.employees_managed, "employee_count": count,
        "invite_code_expires_at": current_user.invite_code_expires_at,
        "fund_account_is_simulated": True}


@router.get("/workspace")
def workspace(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _workspace(current_user, db)


@router.patch("/workspace")
def update_workspace(payload: WorkspaceUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _workspace(current_user, db)
    current_user = db.scalar(select(User).where(User.id == current_user.id).with_for_update().execution_options(populate_existing=True))
    data = _workspace(current_user, db)
    if payload.employees_managed is not None and payload.employees_managed < data["employee_count"]:
        raise HTTPException(status_code=409, detail="Capacity cannot be smaller than the current employee count.")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(current_user, key, value)
    db.commit()
    return _workspace(current_user, db)
