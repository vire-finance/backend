import logging
import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError

import app.core.models

from app.api.pocket.router import router as pocket_router
from app.api.card.router import router as card_router
from app.api.auth.router import router as auth_router
from app.api.profile.router import router as profile_router
from app.api.profile.photo import router as profile_photo_router
from app.api.security.router import router as security_router
from app.api.ocr.router import router as ocr_router
from app.api.request.router import router as request_router
from app.api.transaction.router import router as transaction_router
from app.api.payment.router import router as payment_router
from app.api.notification.router import router as notification_router
from app.api.dashboard.router import router as dashboard_router
from app.api.history.router import router as history_router
from app.api.ai.router import router as ai_router
from app.api.funding.router import router as funding_router
from app.api.notification.worker import run_worker
from app.api.ocr.worker import run_ocr_worker
from app.api.ocr.files import with_storage_file_routes
from app.core.config import settings
from app.core.request_guard import LocalRequestGuardMiddleware
from app.api.ai.provider import close_inference_client
from app.core.storage import StorageError, validate_production_storage


logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(validate_production_storage)
    workers = [asyncio.create_task(run_worker()), asyncio.create_task(run_ocr_worker())]
    try:
        yield
    finally:
        try:
            for worker in workers:
                worker.cancel()
            for worker in workers:
                with suppress(asyncio.CancelledError):
                    await worker
        finally:
            close_inference_client()


app = FastAPI(title="VIRE API", version="1.0.0", lifespan=lifespan)

app.add_middleware(LocalRequestGuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=[
        "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS",
    ],
    allow_headers=[
        "Authorization", "Content-Type", "Idempotency-Key", "X-Action-Token",
    ],
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exception: SQLAlchemyError):
    # SQLAlchemy exceptions may contain bound password/PIN hashes or secrets.
    logger.error("Database error on %s (%s)", request.url.path, type(exception).__name__)
    return JSONResponse(
        status_code=503,
        content={
            "code": "DATABASE_UNAVAILABLE",
            "detail": (
                "Operasi database belum dapat diselesaikan. "
                "Untuk transaksi atau payment, retry dengan "
                "Idempotency-Key yang sama."
            )
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exception: RequestValidationError):
    # FastAPI otherwise echoes rejected input, including passwords/PINs/tokens.
    return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "detail": [
        {key: item[key] for key in ("loc", "msg", "type")} for item in exception.errors()]})

app.include_router(auth_router)
app.include_router(ocr_router)
app.include_router(pocket_router)
app.include_router(card_router)
app.include_router(profile_router)
app.include_router(profile_photo_router)
app.include_router(security_router)

@app.exception_handler(StorageError)
async def storage_error(request: Request, exception: StorageError):
    logger.warning("Receipt storage error code=%s", exception.code)
    missing = exception.code in {"missing", "invalid_reference"}
    return JSONResponse(status_code=404 if missing else 503,
        content={"code": "FILE_NOT_FOUND" if missing else "STORAGE_UNAVAILABLE",
                 "detail": "File tidak tersedia." if missing else "Receipt storage is temporarily unavailable."},
        headers={"Retry-After": "5"} if exception.retryable else None)


app.include_router(with_storage_file_routes(request_router))
app.include_router(transaction_router)
app.include_router(payment_router)
app.include_router(notification_router)
app.include_router(dashboard_router)
app.include_router(history_router)
app.include_router(ai_router)
app.include_router(funding_router)

@app.get("/health")
def health():
    return {"status": "ok"}
