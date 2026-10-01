import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.api.auth.dependencies import get_active_user
from app.api.auth.model import User
from app.api.ocr.schema import (
    OCRDocumentDetailResponse,
    OCRDocumentUploadResponse,
)
from app.api.ocr.service import (
    OCRService,
    process_ocr_document_task,
)
from app.core.database import get_db
from app.shared.enums import UserRole


router = APIRouter(
    prefix="/ocr",
    tags=["OCR"],
)


def get_ocr_service(
    db: Session = Depends(get_db),
) -> OCRService:
    return OCRService(db)


def require_employee(
    current_user: User = Depends(get_active_user),
) -> User:
    if current_user.role != UserRole.EMPLOYEE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only employee accounts can access OCR document scanning.",
        )

    return current_user


@router.post(
    "/documents",
    response_model=OCRDocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(require_employee),
    service: OCRService = Depends(get_ocr_service),
):
    document = await service.upload_document(
        file,
        user_id=current_user.id,
    )

    background_tasks.add_task(
        process_ocr_document_task,
        document.id,
    )

    return OCRDocumentUploadResponse(
        document_id=document.id,
        status=document.ocr_status,
    )


@router.get(
    "/documents/{document_id}",
    response_model=OCRDocumentDetailResponse,
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
)
def get_document_status(
    document_id: uuid.UUID,
    current_user: User = Depends(require_employee),
    service: OCRService = Depends(get_ocr_service),
):
    return service.get_document_detail(
        document_id=document_id,
        user_id=current_user.id,
    )
