"""
API del visor SPHEREx.

  uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2

Endpoints (fov en GRADOS, igual que en la URL del frontend ?ra=&dec=&fov=):
  GET /api/v1/targets                         objetos de prueba pre-calculados
  GET /api/v1/epochs?ra=&dec=                 solo metadatos por época (1-5 s) -> pintar la línea de tiempo ya
  GET /api/v1/sky-patch?ra=&dec=&fov=0.1      timelapse + resta + espectro (cache: ms; en frío: 20-90 s)
  GET /api/v1/spectrum?ra=&dec=               espectro de un clic (recortes de ~18")
  /static/...                                 PNGs generados
"""
import json
import math
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import spherex_core as core
from targets import TARGETS

app = FastAPI(title="SPHEREx Blink API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=core.STATIC_DIR), name="static")

FOV_MIN, FOV_MAX = 0.02, 0.5        # grados (1.2' – 30')


def _check(ra, dec):
    if not (0 <= ra < 360 and -90 <= dec <= 90):
        raise HTTPException(400, "ra en [0,360), dec en [-90,90]")


def _snap(ra, dec, fov):
    """Si las coordenadas caen a <1' de un objeto pre-calculado, usa exactamente su parche."""
    for t in targets():
        if abs(t["dec_deg"] - dec) < 1 / 60 and abs((t["ra_deg"] - ra + 180) % 360 - 180) * math.cos(math.radians(dec)) < 1 / 60:
            return t["ra_deg"], t["dec_deg"], t["fov_deg"], t["name"]
    return ra, dec, fov, None


@app.get("/api/v1/targets")
def targets():
    path = os.path.join(core.STATIC_DIR, "targets.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return TARGETS


@app.get("/api/v1/epochs")
def epochs(ra: float, dec: float):
    _check(ra, dec)
    try:
        rows = core.query_images(ra, dec)
    except Exception as e:
        raise HTTPException(502, f"IRSA no responde: {e}")
    return {"ra_deg": ra, "dec_deg": dec, "n_images": len(rows), "epochs": core.summarize(rows)}


@app.get("/api/v1/sky-patch")
def sky_patch(ra: float, dec: float,
              fov: float = Query(0.1, description="campo de visión en grados"),
              max_per_det: int = Query(12, ge=0, le=200, description="imágenes por época y detector (0 = todas)"),
              name: str | None = None, force: bool = False):
    _check(ra, dec)
    fov = min(max(fov, FOV_MIN), FOV_MAX)
    sra, sdec, sfov, sname = _snap(ra, dec, fov)
    if sname and abs(sfov - fov) < 0.02:          # golden path
        ra, dec, fov, name = sra, sdec, sfov, name or sname
    try:
        return core.build_sky_patch(ra, dec, fov, max_per_det, name=name, force=force)
    except Exception as e:
        raise HTTPException(502, f"Error procesando datos de IRSA: {e}")


@app.get("/api/v1/spectrum")
def spectrum(ra: float, dec: float, max_per_det: int = Query(0, ge=0, le=200)):
    _check(ra, dec)
    try:
        return core.build_spectrum(ra, dec, max_per_det)
    except Exception as e:
        raise HTTPException(502, f"Error procesando datos de IRSA: {e}")
