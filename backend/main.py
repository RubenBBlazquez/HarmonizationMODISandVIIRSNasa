import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from backend.api.routes import router, warm_caches

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # En segundo plano: la API empieza a servir enseguida mientras se calculan países y resumen.
    threading.Thread(target=warm_caches, name="warm-caches", daemon=True).start()
    yield


app = FastAPI(title="Harmonization API", version="0.2.0", lifespan=lifespan)

origins = os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"]
)
# El mapa precarga decenas de miles de detecciones: el GeoJSON comprimido ocupa ~8 veces menos.
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(router)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Harmonization API is running"}
