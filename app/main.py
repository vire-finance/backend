import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

import app.core.models

from app.api.auth.router import router as auth_router
from app.api.ocr.router import router as ocr_router
from app.api.pocket.router import router as pocket_router
from app.api.card.router import router as card_router
from app.api.request.router import router as request_router
from app.api.transaction.router import router as transaction_router
from app.api.payment.router import router as payment_router
from app.api.notification.router import router as notification_router
from app.core.config import settings


logger = logging.getLogger(__name__)

app = FastAPI(title="VIRE API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=[
        "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS",
    ],
    allow_headers=[
        "Authorization", "Content-Type", "Idempotency-Key",
    ],
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exception: SQLAlchemyError):
    logger.error(
        "Database error on %s",
        request.url.path,
        exc_info=(
            type(exception),
            exception,
            exception.__traceback__,
        ),
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "Operasi database belum dapat diselesaikan. "
                "Untuk transaksi atau payment, retry dengan "
                "Idempotency-Key yang sama."
            )
        },
    )

app.include_router(auth_router)
app.include_router(ocr_router)
app.include_router(pocket_router)
app.include_router(card_router)
app.include_router(request_router)
app.include_router(transaction_router)
app.include_router(payment_router)
app.include_router(notification_router)

@app.get("/health")
def health():
    return {"status": "ok"}