from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth.dependencies import get_current_user
from app.api.auth.model import User
from app.api.pocket.model import Pocket
from app.api.profile.schema import EmployeeResponse, ProfileResponse
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
