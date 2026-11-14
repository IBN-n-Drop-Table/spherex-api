from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import sky_patch
from app.services.golden_path import warm_cache


@asynccontextmanager
async def lifespan(_: FastAPI):
    warm_cache()  # fail fast if a cached JSON breaks the contract
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # must stay False while origins is "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sky_patch.router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
