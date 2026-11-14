from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core import errors
from app.core.config import settings
from app.routers import credits, epochs, jobs, reports, sky_patch, spectrum
from app.routers import chat as chat_router  # wow features: own files
from app.routers import game as game_router
from app.services.golden_path import warm_cache
from app.services.live import live_processing_available, warm_up_in_background
from app.services.reports import get_report_service


class PublicStaticFiles(StaticFiles):
    """Static science images readable cross-origin, even without an Origin header.

    Aladin Lite reads pixels through WebGL, which needs CORS. Sending the
    header on every response also keeps a browser-cached copy that was fetched
    without CORS from breaking a later CORS fetch of the same URL.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    warm_cache()  # fail fast if a cached JSON breaks the contract
    get_report_service()  # pick Supabase or the in-memory fallback, log which
    warm_up_in_background()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

errors.install(app)  # one error shape everywhere: {"detail", "code", "errors"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # must stay False while origins is "*"
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Location", "Retry-After"],  # 202 -> job polling URL
)

for module in (sky_patch, spectrum, jobs, epochs, reports, credits):
    app.include_router(module.router, prefix=settings.api_v1_prefix)
app.include_router(credits.router)  # also at /credits: data citation must be easy to find
app.include_router(chat_router.router, prefix=settings.api_v1_prefix)
app.include_router(game_router.router, prefix=settings.api_v1_prefix)

# PNGs written by scripts/generate_golden_path.py (--storage local) and live jobs (static/live/<job_id>/)
settings.static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", PublicStaticFiles(directory=settings.static_dir), name="static")


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "reports_backend": get_report_service().backend,
        "live_processing": "available" if live_processing_available() else "unavailable",
    }
