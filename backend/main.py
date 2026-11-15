import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"

app = FastAPI(title="Harmonization API", version="0.2.0")

origins = os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"]
)
app.include_router(router)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Harmonization API is running"}
