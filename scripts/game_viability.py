#!/usr/bin/env python3
"""Feasibility test for random-sky rounds of "Encuentra lo que se mueve".

Question: if we pick a random position, run the SAME live pipeline the API
uses (scripts/generate_golden_path.py, live settings), how often does the
first-vs-last difference contain a candidate that is really something moving,
how long does it take and how much memory does it use?

For every position (uniform on the sphere, |b| >= --min-abs-b):
  * runs build_payload() in a fresh subprocess with the live-mode arguments
    (app/services/live.py: 1 cutout per detector and visit, 4 workers, no cache),
    timing each pipeline stage and recording the child's peak RSS;
  * records the detector's candidates (detected_anomalies) as they come;
  * checks them against things KNOWN to move:
      - asteroids: IMCCE SkyBoT (astroquery.imcce.Skybot.cone_search) at the
        time of every exposure the pipeline actually used;
      - high proper-motion stars: SIMBAD TAP (same HTTP client as the API),
        stars whose expected shift between the two visits is >= 3".
    A candidate is "real" when it lies within --match-arcsec of one of them.

Usage (from the repo root, with requirements-science.txt installed):
    python scripts/game_viability.py --n 40 --out /tmp/viability
Results: <out>/results.jsonl (one line per position) and <out>/summary.json.
Each position also leaves its PNGs under <out>/app/static/viability/<i>/.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SIMBAD_TAP = os.getenv("SIMBAD_TAP_URL", "https://simbad.cfa.harvard.edu/simbad/sim-tap/sync")


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #


def random_positions(n: int, seed: int, min_abs_b: float) -> list[tuple[float, float, float, float]]:
    """(ra, dec, l, b): RA uniform in [0, 360), dec = asin(2u - 1); |b| < min_abs_b rejected."""
    import numpy as np
    from astropy.coordinates import SkyCoord

    rng = np.random.default_rng(seed)
    out: list[tuple[float, float, float, float]] = []
    while len(out) < n:
        ra = rng.uniform(0.0, 360.0)
        dec = math.degrees(math.asin(2.0 * rng.uniform() - 1.0))
        gal = SkyCoord(ra, dec, unit="deg").galactic
        if abs(gal.b.deg) >= min_abs_b:
            out.append((ra, dec, float(gal.l.deg), float(gal.b.deg)))
    return out


# --------------------------------------------------------------------------- #
# Child: one position
# --------------------------------------------------------------------------- #


def known_movers(ra: float, dec: float, fov_arcmin: float, exposures: list[dict], t_b: float, t_a: float) -> dict:
    """Asteroids (SkyBoT) at each used exposure time and high-PM stars (SIMBAD) in the field."""
    from astropy import units as u
    from astropy.coordinates import SkyCoord
    from astropy.time import Time

    radius_deg = fov_arcmin / 60.0 * math.sqrt(2) / 2 + 0.01
    centre = SkyCoord(ra, dec, unit="deg")
    out: dict = {"asteroids": [], "pm_stars": [], "errors": []}

    t0 = time.monotonic()
    try:
        from astroquery.imcce import Skybot

        for mjd in sorted({round(e["mjd"], 3) for e in exposures}):
            try:
                tab = Skybot.cone_search(centre, radius_deg * u.deg, Time(mjd, format="mjd"))
            except RuntimeError:  # "No solar system object was found in the requested FOV"
                continue
            for row in tab:
                out["asteroids"].append({
                    "name": str(row["Name"]), "mjd": mjd,
                    "ra": float(u.Quantity(row["RA"], u.deg).value),
                    "dec": float(u.Quantity(row["DEC"], u.deg).value),
                    "V": float(u.Quantity(row["V"]).value),
                })
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        out["errors"].append(f"skybot: {type(exc).__name__}: {exc}"[:300])
    out["skybot_s"] = round(time.monotonic() - t0, 2)

    t0 = time.monotonic()
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from app.services.simbad import http_transport

        adql = ("SELECT main_id, ra, dec, pmra, pmdec FROM basic WHERE "
                f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg + 0.1:.4f})) = 1 "
                "AND pmra IS NOT NULL AND pmdec IS NOT NULL "
                "AND SQRT(POWER(pmra, 2) + POWER(pmdec, 2)) > 300")
        payload = http_transport(SIMBAD_TAP, adql, 15.0)
        names = [m["name"] for m in payload["metadata"]]
        years = (t_a - t_b) / 365.25
        for values in payload["data"]:
            r = dict(zip(names, values))
            pm = math.hypot(r["pmra"], r["pmdec"]) / 1000.0
            out["pm_stars"].append({"name": r["main_id"], "ra": r["ra"], "dec": r["dec"],
                                    "pmra": r["pmra"], "pmdec": r["pmdec"],
                                    "shift_arcsec": round(pm * years, 2)})
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"simbad: {type(exc).__name__}: {exc}"[:300])
    out["simbad_s"] = round(time.monotonic() - t0, 2)
    return out


def pm_position(star: dict, mjd: float) -> tuple[float, float]:
    years = (mjd - 51544.5) / 365.25  # SIMBAD basic.ra/dec are J2000
    dec = star["dec"] + star["pmdec"] / 3.6e6 * years
    ra = star["ra"] + star["pmra"] / 3.6e6 * years / math.cos(math.radians(star["dec"]))
    return ra, dec


def sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    d1, d2, da = math.radians(dec1), math.radians(dec2), math.radians(ra1 - ra2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(da)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600.0


def run_one(ra: float, dec: float, fov: float, out_root: Path, index: int, sia_budget: float) -> dict:
    import importlib.util
    import logging

    logging.basicConfig(level=logging.WARNING)
    t_start = time.monotonic()
    spec = importlib.util.spec_from_file_location("gp_pipeline", REPO_ROOT / "scripts" / "generate_golden_path.py")
    pipeline = importlib.util.module_from_spec(spec)
    sys.modules["gp_pipeline"] = pipeline
    spec.loader.exec_module(pipeline)
    pipeline.quiet_astropy()
    t_import = time.monotonic() - t_start

    sys.path.insert(0, str(REPO_ROOT))
    from app.services.live import coordinate_target

    # Same arguments as PipelineLiveRunner._args (app/services/live.py), defaults from app/core/config.py.
    args = pipeline.default_args(fov=fov, max_per_detector=1, workers=4, sia_budget_s=sia_budget,
                                 retry_base_delay=2.0, download_timeout=60.0, no_cache=True, offline=False)

    used: list[dict] = []
    original = pipeline.process_row

    def recording_process_row(row, *a, **kw):
        exp = original(row, *a, **kw)
        if exp is not None:
            used.append({"mjd": exp.mjd, "detector": exp.detector})
        return exp

    pipeline.process_row = recording_process_row

    stages: dict[str, float] = {}

    def progress(stage: str, fraction: float, message: str) -> None:
        stages.setdefault(stage, round(time.monotonic() - t_start, 2))

    result: dict = {"ra": ra, "dec": dec, "fov": fov, "import_s": round(t_import, 2)}
    try:
        payload = pipeline.build_payload(coordinate_target(ra, dec), pipeline.LocalStorage(
            url_path=f"viability/{index}", root=out_root), args, cached=False, progress=progress)
    except Exception as exc:  # noqa: BLE001
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:400], stages=stages,
                      pipeline_s=round(time.monotonic() - t_start, 2))
        result["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        return result
    t_pipeline = time.monotonic() - t_start

    d = payload["difference_imaging"]
    epochs = {e["epoch_id"]: e for e in payload["timelapse_epochs"]}
    from astropy.time import Time

    t_b = Time(epochs[d["epoch_b"]]["date"].replace("Z", "")).mjd
    t_a = Time(epochs[d["epoch_a"]]["date"].replace("Z", "")).mjd
    cands = [{k: a[k] for k in ("anomaly_id", "ra_deg", "dec_deg", "pixel_x", "pixel_y", "snr_confidence",
                                "contamination_flags", "is_clean_detection", "kind")}
             for a in d["detected_anomalies"]]
    result.update(ok=True, stages=stages, pipeline_s=round(t_pipeline, 2),
                  peak_rss_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                  n_visits=len(payload["timelapse_epochs"]), n_cutouts_used=len(used),
                  delta_t_days=round(t_a - t_b, 1), date_b=epochs[d["epoch_b"]]["date"],
                  date_a=epochs[d["epoch_a"]]["date"], candidates=cands,
                  diff_png=d["subtracted_image_url"])

    movers = known_movers(ra, dec, fov, used, t_b, t_a)
    result["movers"] = movers
    # Which candidates sit on a known mover?
    for c in cands:
        c["matches"] = []
        for ast in movers["asteroids"]:
            s = sep_arcsec(c["ra_deg"], c["dec_deg"], ast["ra"], ast["dec"])
            if s <= MATCH_ARCSEC:
                c["matches"].append(f"asteroid {ast['name']} (V={ast['V']:.1f}, {s:.0f}\")")
        for star in movers["pm_stars"]:
            if star["shift_arcsec"] < 3.0:
                continue
            for mjd in (t_b, t_a):
                s = sep_arcsec(c["ra_deg"], c["dec_deg"], *pm_position(star, mjd))
                if s <= MATCH_ARCSEC:
                    c["matches"].append(f"pm star {star['name']} ({star['shift_arcsec']}\" shift, {s:.0f}\")")
                    break
    result["total_s"] = round(time.monotonic() - t_start, 2)
    return result


MATCH_ARCSEC = 18.0  # 3 native SPHEREx pixels


# --------------------------------------------------------------------------- #
# Parent
# --------------------------------------------------------------------------- #


def summarize(rows: list[dict]) -> dict:
    import numpy as np

    ok = [r for r in rows if r.get("ok")]

    def pct(k: int, n: int) -> float:
        return round(100.0 * k / n, 1) if n else 0.0

    def stats(values: list[float]) -> dict:
        if not values:
            return {}
        v = np.asarray(values, float)
        return {"min": round(float(v.min()), 1), "median": round(float(np.median(v)), 1),
                "p90": round(float(np.percentile(v, 90)), 1), "max": round(float(v.max()), 1)}

    any_c = [r for r in ok if r["candidates"]]
    clean_c = [r for r in ok if any(c["is_clean_detection"] for c in r["candidates"])]
    real_c = [r for r in ok if any(c["matches"] for c in r["candidates"])]
    with_ast = [r for r in ok if r["movers"]["asteroids"]]
    with_pm = [r for r in ok if any(s["shift_arcsec"] >= 3 for s in r["movers"]["pm_stars"])]
    return {
        "positions": len(rows),
        "pipeline_ok": len(ok),
        "failures": [r.get("error") for r in rows if not r.get("ok")],
        "pct_with_any_candidate": pct(len(any_c), len(ok)),
        "pct_with_clean_candidate": pct(len(clean_c), len(ok)),
        "pct_with_real_mover_candidate": pct(len(real_c), len(ok)),
        "pct_with_known_asteroid_in_field": pct(len(with_ast), len(ok)),
        "pct_with_pm_star_shift_ge_3arcsec": pct(len(with_pm), len(ok)),
        "candidates_per_position": stats([len(r["candidates"]) for r in ok]),
        "pipeline_s_all": stats([r["pipeline_s"] for r in rows if "pipeline_s" in r]),
        "pipeline_s_ok": stats([r["pipeline_s"] for r in ok]),
        "wall_s_with_subprocess": stats([r["wall_s"] for r in rows if "wall_s" in r]),
        "import_s": stats([r["import_s"] for r in rows if "import_s" in r]),
        "download_stage_s": stats([r["stages"]["stack"] - r["stages"]["download"] for r in ok
                                   if "stack" in r["stages"] and "download" in r["stages"]]),
        "peak_rss_mb": stats([r["peak_rss_mb"] for r in rows if "peak_rss_mb" in r]),
        "n_visits": stats([r["n_visits"] for r in ok]),
        "delta_t_days": stats([r["delta_t_days"] for r in ok]),
        "cutouts_used": stats([r["n_cutouts_used"] for r in ok]),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--seed", type=int, default=1930)
    p.add_argument("--fov", type=float, default=5.0, help="arcmin (Golden Path and game use 5)")
    p.add_argument("--min-abs-b", type=float, default=15.0, help="skip the galactic plane |b| < this")
    p.add_argument("--sia-budget", type=float, default=10.0, help="s; the API uses SIA_BUDGET_S=5")
    p.add_argument("--timeout", type=float, default=300.0, help="hard cap per position (s)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--one", nargs=2, type=float, metavar=("RA", "DEC"), help=argparse.SUPPRESS)
    p.add_argument("--index", type=int, default=0, help=argparse.SUPPRESS)
    a = p.parse_args()

    if a.one:
        res = run_one(a.one[0], a.one[1], a.fov, a.out, a.index, a.sia_budget)
        print("RESULT " + json.dumps(res, default=str), flush=True)
        return 0

    a.out.mkdir(parents=True, exist_ok=True)
    results_file = a.out / "results.jsonl"
    done = {}
    if results_file.is_file():  # resumable
        for line in results_file.read_text().splitlines():
            r = json.loads(line)
            done[r["index"]] = r
    rows = list(done.values())
    for i, (ra, dec, gl, gb) in enumerate(random_positions(a.n, a.seed, a.min_abs_b)):
        if i in done:
            continue
        t0 = time.monotonic()
        cmd = [sys.executable, __file__, "--one", f"{ra}", f"{dec}", "--index", str(i), "--fov", str(a.fov),
               "--sia-budget", str(a.sia_budget), "--out", str(a.out)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout, cwd=REPO_ROOT)
            line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None)
            res = json.loads(line[7:]) if line else {
                "ra": ra, "dec": dec, "ok": False, "error": f"no result (rc={proc.returncode}): "
                + proc.stderr[-400:]}
        except subprocess.TimeoutExpired:
            res = {"ra": ra, "dec": dec, "ok": False, "error": f"timeout after {a.timeout:.0f} s"}
        res.update(index=i, l=round(gl, 2), b=round(gb, 2), wall_s=round(time.monotonic() - t0, 2))
        rows.append(res)
        with results_file.open("a") as fh:
            fh.write(json.dumps(res, default=str) + "\n")
        n_c = len(res.get("candidates", []))
        n_real = sum(1 for c in res.get("candidates", []) if c.get("matches"))
        print(f"[{i + 1:>2}/{a.n}] ra={ra:7.3f} dec={dec:+7.3f} b={gb:+6.1f} ok={res.get('ok')} "
              f"wall={res['wall_s']:.0f}s rss={res.get('peak_rss_mb', '?')}MB cand={n_c} real={n_real} "
              f"ast={len(res.get('movers', {}).get('asteroids', []))} {res.get('error', '')[:80]}", flush=True)

    summary = summarize(sorted(rows, key=lambda r: r["index"]))
    (a.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
