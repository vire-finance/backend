from fastapi import FastAPI

from app.api.pocket.router import router as pocket_router
from app.api.card.router import router as card_router
from app.api.auth.router import router as auth_router
from app.api.profile.router import router as profile_router
from app.api.security.router import router as security_router
from app.api.ocr.router import router as ocr_router


app = FastAPI()

app.include_router(pocket_router)
app.include_router(card_router)
app.include_router(auth_router)
app.include_router(ocr_router)
app.include_router(profile_router)
app.include_router(security_router)

