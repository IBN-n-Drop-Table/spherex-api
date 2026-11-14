"""
Motor SPHEREx para el visor: consulta -> recortes en paralelo -> coadd por época
semestral y por detector -> PNG + resta + espectro + curva de luz.

Claves de velocidad:
  * NUNCA se descargan los MEF completos (2040x2040, decenas de MB cada uno):
    se piden recortes con ?center=RA,DECd&size=DEG al servicio de cutouts de IRSA.
  * Descargas en paralelo (ThreadPoolExecutor).
  * Caché en disco de cada recorte (por URL) y de cada resultado JSON (por ra/dec/fov).
  * Límite de imágenes por (época, detector) configurable (max_per_det).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning
from astroquery.ipac.irsa import Irsa
from PIL import Image
from reproject import reproject_interp
from scipy import ndimage

warnings.simplefilter("ignore", FITSFixedWarning)
warnings.simplefilter("ignore", RuntimeWarning)
try:
    from erfa import ErfaWarning
    warnings.simplefilter("ignore", ErfaWarning)
except Exception:
    pass

# --------------------------------------------------------------------------- #
# Configuración (todo sobreescribible por variables de entorno)
# --------------------------------------------------------------------------- #
COLLECTIONS = os.getenv("SPHEREX_COLLECTIONS", "spherex_dr1,spherex_qr3").split(",")
EPOCH0 = Time(os.getenv("SPHEREX_EPOCH0", "2025-05-01T00:00:00"), scale="utc")
SEMESTER_DAYS = 182.625
PIXSCALE = 6.15 / 3600.0                       # deg/px nativo SPHEREx
CACHE_DIR = os.getenv("SPHEREX_CACHE", "cache")
STATIC_DIR = os.getenv("SPHEREX_STATIC", "static")
PUBLIC_BASE = os.getenv("PUBLIC_BASE", "/static")   # p.ej. https://tu-servidor.com/static
WORKERS = int(os.getenv("SPHEREX_WORKERS", "24"))
# Bits de FLAGS que NO se descartan (SOURCE marca píxeles sobre fuentes: ¡no tirarlos!)
KEEP_FLAGS = set(os.getenv("SPHEREX_KEEP_FLAGS", "SOURCE").split(","))

DETECTORS = [f"D{i}" for i in range(1, 7)]
# Filtros "químicos" (µm). Hielo H2O ~3.05, CO2 ~4.27, CO ~4.67.
CHEM_FILTERS = {"H2O": (2.95, 3.15), "CO2": (4.20, 4.34), "CO": (4.60, 4.74)}
# Composición RGB: R=D6 (4.4-5 µm), G=D4 (2.4-3.8 µm), B=D2 (1.1-1.6 µm).
# Las enanas marrones frías salen muy rojas.
RGB = ("D6", "D4", "D2")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
_session = requests.Session()
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


# --------------------------------------------------------------------------- #
# 1. Consulta de metadatos (rápida, 1-5 s)
# --------------------------------------------------------------------------- #
def epoch_of(mjd: float) -> int:
    return int((mjd - EPOCH0.mjd) // SEMESTER_DAYS)


def epoch_label(k: int) -> tuple[str, str, str]:
    t0 = EPOCH0 + k * SEMESTER_DAYS * u.day
    t1 = t0 + SEMESTER_DAYS * u.day
    return f"E{k+1}_{t0.datetime:%Y-%m}", t0.isot[:10], t1.isot[:10]


def query_images(ra: float, dec: float) -> list[dict]:
    """Todas las imágenes L2 que contienen (ra, dec), sin duplicados DR1/QR."""
    coord = SkyCoord(ra, dec, unit="deg")
    tab = Irsa.query_sia(pos=(coord, 1 * u.arcsec), collection=COLLECTIONS)
    rows = []
    for r in tab:
        band = str(r["energy_bandpassname"])            # 'SPHEREx-D2'
        det = band.split("-")[-1]
        if det not in DETECTORS:
            continue
        rows.append({
            "obs_id": str(r["obs_id"]),
            "collection": str(r["obs_collection"]),
            "det": det,
            "mjd": float(r["t_min"]),
            "url": str(r["access_url"]),
        })
    # Preferimos DR1 (calibración definitiva) frente a QR si la misma obs está en ambas
    rows.sort(key=lambda d: (0 if "dr" in d["collection"] else 1))
    seen, out = set(), []
    for d in rows:
        k = (d["obs_id"], d["det"])
        if k in seen:
            continue
        seen.add(k)
        d["epoch"] = epoch_of(d["mjd"])
        out.append(d)
    out.sort(key=lambda d: d["mjd"])
    return out


def summarize(rows: list[dict]) -> list[dict]:
    """Resumen por época (para que el frontend pinte la línea de tiempo al instante)."""
    out = []
    for k in sorted({d["epoch"] for d in rows}):
        sel = [d for d in rows if d["epoch"] == k]
        eid, a, b = epoch_label(k)
        out.append({
            "epoch_id": eid, "date_start": a, "date_end": b,
            "date": Time(np.median([d["mjd"] for d in sel]), format="mjd").isot[:19] + "Z",
            "n_images": len(sel),
            "n_per_detector": {det: sum(d["det"] == det for d in sel) for det in DETECTORS},
        })
    return out


def select(rows: list[dict], max_per_det: int) -> list[dict]:
    """Limita imágenes por (época, detector), repartidas uniformemente."""
    out = []
    for k in {d["epoch"] for d in rows}:
        for det in DETECTORS:
            sel = [d for d in rows if d["epoch"] == k and d["det"] == det]
            if max_per_det and len(sel) > max_per_det:
                idx = np.linspace(0, len(sel) - 1, max_per_det).round().astype(int)
                sel = [sel[i] for i in sorted(set(idx))]
            out.extend(sel)
    return out


# --------------------------------------------------------------------------- #
# 2. Descarga de recortes (en paralelo + caché en disco)
# --------------------------------------------------------------------------- #
def cutout_url(url: str, ra: float, dec: float, size_deg: float) -> str:
    return f"{url.split('?')[0]}?center={ra:.6f},{dec:.6f}d&size={size_deg:.5f}"


def fetch(url: str, retries: int = 3) -> bytes:
    path = os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".fits")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    for k in range(retries):
        try:
            r = _session.get(url, timeout=90)
            r.raise_for_status()
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(r.content)
            os.replace(tmp, path)
            return r.content
        except Exception:
            if k == retries - 1:
                raise
            time.sleep(2 * (k + 1))


def load_cutout(meta: dict, ra: float, dec: float, size_deg: float) -> dict | None:
    try:
        raw = fetch(cutout_url(meta["url"], ra, dec, size_deg))
        with fits.open(io.BytesIO(raw)) as h:
            return parse_mef(h, meta)
    except Exception as e:  # una imagen rota no debe tumbar la petición
        print("cutout fallido:", meta["obs_id"], meta["det"], e)
        return None


def parse_mef(h: fits.HDUList, meta: dict) -> dict:
    hdr = h["IMAGE"].header
    img = h["IMAGE"].data.astype("f8")
    var = h["VARIANCE"].data.astype("f8")
    flags = h["FLAGS"].data.astype("i8")
    if "ZODI" in h:
        img = img - h["ZODI"].data

    # Máscara de rechazo a partir de los nombres de bit (MSKNnn) de la cabecera FLAGS
    fh = h["FLAGS"].header
    names = {int(k[4:]): fh[k] for k in fh if k.startswith("MSKN")}
    reject = 0
    for bit, name in names.items():
        if name not in KEEP_FLAGS:
            reject |= 1 << bit
    if not names:
        reject = ~0
    bad = ((flags & reject) != 0) | ~np.isfinite(img) | ~np.isfinite(var) | (var <= 0)

    # Longitud de onda por píxel (filtro LVF) vía WCS espectral (clave 'W')
    wl = bw = None
    try:
        sw = WCS(header=hdr, fobj=h, key="W")
        sw.sip = None
        yy, xx = np.indices(img.shape)
        w_, b_ = sw.pixel_to_world(xx, yy)
        wl = np.asarray(getattr(w_, "value", w_), dtype="f8")
        bw = np.asarray(getattr(b_, "value", b_), dtype="f8")
    except Exception:
        pass

    return {**meta, "img": img, "var": var, "flags": flags, "bad": bad,
            "wcs": WCS(hdr), "wl": wl, "bw": bw, "flag_names": names}


def load_many(metas: list[dict], ra: float, dec: float, size_deg: float) -> list[dict]:
    out = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(load_cutout, m, ra, dec, size_deg) for m in metas]
        for f in as_completed(futs):
            r = f.result()
            if r is not None:
                out.append(r)
    return out


# --------------------------------------------------------------------------- #
# 3. Coadds
# --------------------------------------------------------------------------- #
def target_wcs(ra: float, dec: float, fov_deg: float) -> tuple[WCS, tuple[int, int]]:
    n = max(8, int(np.ceil(fov_deg / PIXSCALE)))
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    w.wcs.cdelt = [-PIXSCALE, PIXSCALE]
    return w, (n, n)


def coadd(images: list[dict], wout: WCS, shape, wl_range=None) -> dict | None:
    num = np.zeros(shape); den = np.zeros(shape)
    nh = np.zeros(shape, "i4"); fl = np.zeros(shape, "i8")
    used = 0
    for im in images:
        bad = im["bad"].copy()
        if wl_range is not None:
            if im["wl"] is None:
                continue
            bad |= (im["wl"] < wl_range[0]) | (im["wl"] > wl_range[1])
            if bad.all():
                continue
        data = np.where(bad, np.nan, im["img"])
        wgt = np.where(bad, np.nan, 1.0 / im["var"])
        d_r, _ = reproject_interp((data, im["wcs"]), wout, shape_out=shape)
        w_r, _ = reproject_interp((wgt, im["wcs"]), wout, shape_out=shape)
        f_r, _ = reproject_interp((im["flags"].astype("f8"), im["wcs"]), wout,
                                  shape_out=shape, order="nearest-neighbor")
        ok = np.isfinite(d_r) & np.isfinite(w_r) & (w_r > 0)
        if not ok.any():
            continue
        used += 1
        num[ok] += d_r[ok] * w_r[ok]
        den[ok] += w_r[ok]
        nh[ok] += 1
        fin = np.isfinite(f_r)
        fl[fin] |= f_r[fin].astype("i8")
    if used == 0:
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        img = np.where(den > 0, num / den, np.nan)
    return {"img": img, "w": den, "nhits": nh, "flags": fl, "n": used}


# --------------------------------------------------------------------------- #
# 4. Conversión a PNG (misma escala en todas las épocas -> blink honesto)
# --------------------------------------------------------------------------- #
def _stretch(a, lo, hi):
    x = np.clip((a - lo) / (hi - lo + 1e-12), 0, 1)
    x = np.arcsinh(10 * x) / np.arcsinh(10)
    return np.nan_to_num(x, nan=0.0)


def limits(arrays):
    v = np.concatenate([a[np.isfinite(a)].ravel() for a in arrays if a is not None] or [np.zeros(1)])
    if v.size < 2:
        return 0.0, 1.0
    return float(np.percentile(v, 1)), float(np.percentile(v, 99.7))


def save_png(arr01, path):
    """arr01: (ny,nx) o (ny,nx,3) en [0,1], origen abajo (FITS) -> PNG con origen arriba."""
    a = (np.flipud(arr01) * 255).astype("u1")
    Image.fromarray(a).save(path, optimize=True)


def save_diverging(snr, path, lim=5.0):
    x = np.clip(np.nan_to_num(snr) / lim, -1, 1)
    r = np.where(x > 0, 1, 1 + x); g = 1 - np.abs(x); b = np.where(x < 0, 1, 1 - x)
    save_png(np.dstack([r, g, b]), path)


# --------------------------------------------------------------------------- #
# 5. Resta entre épocas y detección de candidatos a movimiento
# --------------------------------------------------------------------------- #
def difference(c1: dict, c2: dict, wout: WCS, flag_names: dict, thresh=5.0, min_px=2):
    with np.errstate(invalid="ignore", divide="ignore"):
        diff = c2["img"] - c1["img"]
        sig = np.sqrt(1.0 / c1["w"] + 1.0 / c2["w"])
        # quitar el offset de fondo residual
        diff -= np.nanmedian(diff)
        snr = diff / sig
    anomalies = []
    for sign in (+1, -1):
        mask = np.nan_to_num(sign * snr) > thresh
        lab, n = ndimage.label(mask)
        for i in range(1, n + 1):
            yy, xx = np.nonzero(lab == i)
            if yy.size < min_px:
                continue
            wts = np.abs(snr[yy, xx])
            y0 = float(np.average(yy, weights=wts)); x0 = float(np.average(xx, weights=wts))
            sky = wout.pixel_to_world(x0, y0)
            f = int(c1["flags"][int(round(y0)), int(round(x0))] | c2["flags"][int(round(y0)), int(round(x0))])
            fnames = [nm for b, nm in flag_names.items() if f & (1 << b)]
            clean = all(nm in KEEP_FLAGS for nm in fnames)
            anomalies.append({
                "anomaly_id": f"anom_{len(anomalies)+1:02d}",
                "kind": "appeared" if sign > 0 else "disappeared",
                "ra_deg": round(float(sky.ra.deg), 6), "dec_deg": round(float(sky.dec.deg), 6),
                "pixel_x": round(x0, 1), "pixel_y": round(snr.shape[0] - 1 - y0, 1),  # coords PNG
                "snr_confidence": round(float(wts.max()), 1),
                "contamination_flags": fnames, "is_clean_detection": clean,
            })
    anomalies.sort(key=lambda a: -a["snr_confidence"])
    return snr, anomalies


# --------------------------------------------------------------------------- #
# 6. Espectro y curva de luz en un punto (a partir de los recortes ya bajados)
# --------------------------------------------------------------------------- #
def spectrum_at(images: list[dict], ra: float, dec: float) -> list[dict]:
    c = SkyCoord(ra, dec, unit="deg")
    out = []
    for im in images:
        x, y = im["wcs"].world_to_pixel(c)
        xi, yi = int(round(float(x))), int(round(float(y)))
        ny, nx = im["img"].shape
        if not (0 <= xi < nx and 0 <= yi < ny) or im["bad"][yi, xi] or im["wl"] is None:
            continue
        # suma 3x3 (aprox. a la PSF) en MJy/sr*px; usa el píxel central para wl
        sl = (slice(max(0, yi-1), yi+2), slice(max(0, xi-1), xi+2))
        good = ~im["bad"][sl]
        out.append({
            "wavelength_um": round(float(im["wl"][yi, xi]), 4),
            "bandwidth_um": round(float(im["bw"][yi, xi]), 4),
            "flux_mjy_sr": float(np.nansum(np.where(good, im["img"][sl], 0))),
            "variance": float(np.nansum(np.where(good, im["var"][sl], 0))),
            "detector": int(im["det"][1]),
            "epoch": epoch_label(im["epoch"])[0],
            "date": Time(im["mjd"], format="mjd").isot[:19] + "Z",
        })
    out.sort(key=lambda d: d["wavelength_um"])
    return out


def light_curve(spec: list[dict]) -> list[dict]:
    """Mediana del flujo por (época, detector): sirve para variables (RW Aur, R Dra)."""
    out = []
    for ep in sorted({s["epoch"] for s in spec}):
        for d in range(1, 7):
            v = [s["flux_mjy_sr"] for s in spec if s["epoch"] == ep and s["detector"] == d]
            if v:
                out.append({"epoch": ep, "detector": d, "median_flux": float(np.median(v)), "n": len(v)})
    return out


# --------------------------------------------------------------------------- #
# 7. Orquestación: todo lo que pide el frontend en un JSON
# --------------------------------------------------------------------------- #
def cache_key(ra, dec, fov, max_per_det=None):
    # max_per_det no entra en la clave: si ya hay un resultado (p.ej. completo) se reutiliza
    return f"{ra:.4f}_{dec:+.4f}_{fov:.3f}".replace("+", "p").replace("-", "m")


def build_sky_patch(ra: float, dec: float, fov_deg: float = 0.1, max_per_det: int = 12,
                    name: str | None = None, force: bool = False) -> dict:
    key = cache_key(ra, dec, fov_deg, max_per_det)
    jpath = os.path.join(STATIC_DIR, key, "result.json")
    with _lock_for(key):                      # dos usuarios, misma zona -> un solo cálculo
        if os.path.exists(jpath) and not force:
            with open(jpath) as f:
                res = json.load(f)
            res["target_info"]["is_cached_golden_path"] = True
            return res

        t_start = time.time()
        rows = query_images(ra, dec)
        metas = select(rows, max_per_det)
        size = fov_deg * 1.15                 # margen para la rotación de los recortes
        images = load_many(metas, ra, dec, size)
        wout, shape = target_wcs(ra, dec, fov_deg)
        outdir = os.path.join(STATIC_DIR, key); os.makedirs(outdir, exist_ok=True)
        url = lambda fn: f"{PUBLIC_BASE}/{key}/{fn}"
        flag_names = next((im["flag_names"] for im in images if im["flag_names"]), {})

        epochs = sorted({im["epoch"] for im in images})
        co = {(k, d): coadd([im for im in images if im["epoch"] == k and im["det"] == d], wout, shape)
              for k in epochs for d in DETECTORS}
        chem = {(k, f): coadd([im for im in images if im["epoch"] == k], wout, shape, wl_range=rng)
                for k in epochs for f, rng in CHEM_FILTERS.items()}

        # límites comunes por detector / filtro para todas las épocas
        lim_det = {d: limits([co[(k, d)]["img"] for k in epochs if co[(k, d)]]) for d in DETECTORS}
        lim_chem = {f: limits([chem[(k, f)]["img"] for k in epochs if chem[(k, f)]]) for f in CHEM_FILTERS}

        timelapse = []
        summ = {s["epoch_id"]: s for s in summarize(rows)}
        for k in epochs:
            eid, a, b = epoch_label(k)
            det_urls = {}
            for d in DETECTORS:
                c = co[(k, d)]
                if c:
                    fn = f"{eid}_{d}.png"; save_png(_stretch(c["img"], *lim_det[d]), os.path.join(outdir, fn))
                    det_urls[d] = url(fn)
            rgb_url = None
            if all(co[(k, d)] for d in RGB):
                rgb = np.dstack([_stretch(co[(k, d)]["img"], *lim_det[d]) for d in RGB])
                fn = f"{eid}_rgb.png"; save_png(rgb, os.path.join(outdir, fn)); rgb_url = url(fn)
            chem_urls = {}
            for f in CHEM_FILTERS:
                c = chem[(k, f)]
                if c:
                    fn = f"{eid}_{f}.png"; save_png(_stretch(c["img"], *lim_chem[f]), os.path.join(outdir, fn))
                    chem_urls[f] = url(fn)
            timelapse.append({
                "epoch_id": eid, "date_start": a, "date_end": b,
                "date": summ.get(eid, {}).get("date"),
                "n_images_used": sum(1 for im in images if im["epoch"] == k),
                "n_images_available": summ.get(eid, {}).get("n_images"),
                "image_url": rgb_url or next(iter(det_urls.values()), None),
                "detectors": det_urls,
                "chemical_filters": chem_urls,
            })

        # Resta: primera vs última época (más tiempo -> más desplazamiento)
        diff_block = {"subtracted_image_url": None, "per_detector": {}, "detected_anomalies": []}
        if len(epochs) >= 2:
            k1, k2 = epochs[0], epochs[-1]
            for d in DETECTORS:
                if co[(k1, d)] and co[(k2, d)]:
                    snr, anoms = difference(co[(k1, d)], co[(k2, d)], wout, flag_names)
                    fn = f"diff_{d}.png"; save_diverging(snr, os.path.join(outdir, fn))
                    for a_ in anoms:
                        a_["detector"] = d
                    diff_block["per_detector"][d] = {"url": url(fn), "anomalies": anoms}
            # Imagen de resta "principal": D2 (más estrellas, mejor PSF); si no, la que haya
            best = next((d for d in ("D2", "D3", "D1", "D6", "D4", "D5")
                         if d in diff_block["per_detector"]), None)
            if best:
                diff_block["subtracted_image_url"] = diff_block["per_detector"][best]["url"]
                diff_block["detector"] = best
                diff_block["epochs_compared"] = [epoch_label(k1)[0], epoch_label(k2)[0]]
                diff_block["detected_anomalies"] = diff_block["per_detector"][best]["anomalies"]

        spec = spectrum_at(images, ra, dec)
        res = {
            "target_info": {"name": name, "ra_deg": ra, "dec_deg": dec,
                            "fov_arcmin": round(fov_deg * 60, 2), "pixel_arcsec": 6.15,
                            "image_size_px": shape[0], "is_cached_golden_path": False,
                            "processing_seconds": round(time.time() - t_start, 1),
                            "n_images_available": len(rows), "n_images_used": len(images)},
            "timelapse_epochs": timelapse,
            "difference_imaging": diff_block,
            "spectrum_102_bands": spec,
            "light_curve": light_curve(spec),
        }
        with open(jpath, "w") as f:
            json.dump(res, f)
        return res


def build_spectrum(ra: float, dec: float, max_per_det: int = 0) -> dict:
    """Espectro en un punto con recortes diminutos (≈18"): rápido incluso sin límite."""
    rows = select(query_images(ra, dec), max_per_det)
    images = load_many(rows, ra, dec, 0.005)
    spec = spectrum_at(images, ra, dec)
    return {"ra_deg": ra, "dec_deg": dec, "spectrum_102_bands": spec, "light_curve": light_curve(spec)}
