from fastapi import FastAPI

from app.api.pocket.router import router as pocket_router
from app.api.card.router import router as card_router


app = FastAPI()

app.include_router(pocket_router)
app.include_router(card_router)