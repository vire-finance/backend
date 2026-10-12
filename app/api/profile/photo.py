"""Authenticated profile images using the existing private storage backend."""
import io
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.auth.dependencies import get_active_user
from app.api.auth.model import User
from app.api.ocr.files import ReceiptFileResponse
from app.core.database import get_db
from app.core.storage import get_receipt_storage, storage_for_reference
from app.api.profile.model import ProfilePhoto
from app.shared.utils import ensure

logger = logging.getLogger(__name__)
MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_PHOTO_PIXELS = 16_000_000
router = APIRouter(prefix='/api/v1', tags=['Profile'])


def normalize_photo(content, mime):
    ensure(0 < len(content) <= MAX_PHOTO_BYTES, 'Profile photo must be at most 2 MB.', 413)
    ensure(mime in {'image/jpeg', 'image/png'}, 'Use a JPEG or PNG profile photo.', 415)
    try:
        with Image.open(io.BytesIO(content)) as source:
            ensure(source.format == ('JPEG' if mime == 'image/jpeg' else 'PNG'), 'Invalid photo format.', 415)
            ensure(source.width * source.height <= MAX_PHOTO_PIXELS, 'Profile photo dimensions are too large.', 413)
            source.load()
            with ImageOps.exif_transpose(source) as oriented:
                oriented.thumbnail((1024, 1024))
                with oriented.convert('RGB') as image:
                    output = io.BytesIO()
                    # Re-encoding strips metadata and does not retain arbitrary upload bytes.
                    image.save(output, format='JPEG', quality=85)
                    return output.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(415, 'Invalid photo content.') from None


def save_photo(content, user, db):
    storage = get_receipt_storage()
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    old = db.scalar(select(ProfilePhoto).where(ProfilePhoto.user_id == user.id))
    old_reference = old.storage_path if old else None
    identifier = uuid4()
    filename = f'{identifier}.jpg'
    reference = storage.save(content, user.id, filename, 'image/jpeg')
    committing = False
    try:
        if old:
            db.delete(old)
            db.flush()
        db.add(ProfilePhoto(id=identifier, user_id=user.id, storage_path=reference,
            stored_filename=filename, original_filename='profile.jpg', mime_type='image/jpeg', file_size=len(content)))
        user.avatar_url = f'/api/v1/profiles/{user.id}/photo'
        db.flush()
        committing = True
        db.commit()
    except Exception:
        db.rollback()
        # A lost commit acknowledgement is ambiguous: never delete a possibly
        # referenced new image. A known pre-commit failure can be cleaned up.
        if not committing:
            try:
                storage.delete(reference)
            except Exception:
                logger.warning('Profile photo cleanup deferred')
        else:
            logger.warning('Profile photo commit outcome uncertain; file retained')
        raise
    if old_reference:
        try:
            storage_for_reference(old_reference).delete(old_reference)
        except Exception:
            logger.warning('Previous profile photo cleanup deferred')
    return {'avatar_url': user.avatar_url}


@router.post('/profile/photo')
async def upload_photo(file: UploadFile = File(...), user: User = Depends(get_active_user), db: Session = Depends(get_db)):
    try:
        content = await file.read(MAX_PHOTO_BYTES + 1)
        mime = (file.content_type or '').lower().split(';')[0].strip()
        normalized = await run_in_threadpool(normalize_photo, content, mime)
        return await run_in_threadpool(save_photo, normalized, user, db)
    finally:
        await file.close()


@router.get('/profiles/{user_id}/photo')
def profile_photo(user_id: UUID, user: User = Depends(get_active_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    def business(person):
        return person.id if person.role and person.role.value == 'OWNER' else person.employer_id
    ensure(target is not None and (user.id == user_id or
           (business(user) is not None and business(user) == business(target))), 'Profile photo not found.', 404)
    photo = db.scalar(select(ProfilePhoto).where(ProfilePhoto.user_id == user_id))
    ensure(photo is not None, 'Profile photo not found.', 404)
    return ReceiptFileResponse(photo)
