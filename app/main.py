from fastapi import FastAPI
from app.core.database import engine
from app.shared.base import Base
from app.api.auth import model

Base.metadata.create_all(bind=engine)

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello, FastAPI!"}