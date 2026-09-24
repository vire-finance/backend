from fastapi import FastAPI

app = FastAPI(title="My App API")

@app.get("/")
def read_root():
    return {"message": "Hello, FastAPI!"}