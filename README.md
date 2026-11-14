# spherex-api

Backend for the SPHEREx citizen-science app (NASA Space Apps 2026).
**MVP phase:** the API serves pre-processed "Golden Path" targets from local JSON. It does not call NASA, use a database or use Astropy.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

- Interactive docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

```bash
curl "http://localhost:8000/api/v1/sky-patch?target=WISE_0855"
curl "http://localhost:8000/api/v1/sky-patch?ra=133.795&dec=-7.245&fov=5"
curl -i "http://localhost:8000/api/v1/sky-patch?target=M31"   # 404
```

To share it with frontend devs on the same network, use `--host 0.0.0.0`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Endpoint

`GET /api/v1/sky-patch`. All query params are optional:

| param    | type  | notes                                   |
|----------|-------|-----------------------------------------|
| `target` | str   | `WISE_0855` (case, spaces and hyphens are ignored) |
| `ra`     | float | degrees, 0–360                           |
| `dec`    | float | degrees, −90–90                          |
| `fov`    | float | arcmin. Accepted but not used yet      |

The endpoint returns 200 when `target` matches **or** (`ra`, `dec`) falls inside a cached target's box. Otherwise it returns
404 `{"detail": "Target not found in Golden Path cache. Try WISE_0855."}`.
An out-of-range value (e.g. `dec=200`) returns 422.

## Layout

```
app/
  main.py               # FastAPI app, CORS, router wiring, startup cache warm-up
  core/config.py        # settings (CORS_ORIGINS env var, default "*")
  core/targets.py       # the 5 targets: coordinates, astrometry, lookup boxes
  schemas/sky_patch.py  # Pydantic response contract
  routers/sky_patch.py  # HTTP layer
  services/golden_path.py  # lookup over cached targets
  data/<target>.json    # generated Golden Path payloads
  static/golden_path/   # generated PNGs (--storage local)
scripts/generate_golden_path.py  # offline IRSA -> JSON/PNG pipeline
tests/
```

## Golden Path pipeline (offline)

`scripts/generate_golden_path.py` builds `app/data/<target>.json` and the PNGs from real SPHEREx Level 2 data on IRSA. Run it before the demo, not during it.

```bash
pip install -r requirements-science.txt

# Quick smoke test (~2 min): thin each epoch to 24 exposures
python scripts/generate_golden_path.py --target WISE_0855 --max-exposures-per-epoch 24

# Full run for one target (~10 min, ~230 cutouts)
python scripts/generate_golden_path.py --target WISE_0855

# All 5 targets
python scripts/generate_golden_path.py --all
```

With `--storage local` (the default), PNGs go to `app/static/golden_path/` and the API serves them at `http://localhost:8000/static/...`. If the frontend uses a different host, set `--public-base-url`.

To upload to Supabase instead, the bucket must be **public**:

```bash
export SUPABASE_URL="https://<project>.supabase.co"
export SUPABASE_KEY="<service-role key>"   # needs write access to Storage
export SUPABASE_BUCKET="golden-path"
python scripts/generate_golden_path.py --all --storage supabase
```

Downloaded cutouts are stored in astropy's download cache (`~/.astropy/cache`), so a re-run only reprocesses them. To download them again, pass `--no-cache`. For all options, run `--help`.

### What it does

1. **Finds the exposures.** It runs an IRSA SIA query on `spherex_dr1` and `spherex_qr3`. If those have too few exposures, it also queries the deep-field collections. Duplicates across releases are removed.
2. **Picks two epochs.** Exposures are grouped into survey visits by `t_min`. The script picks the best-covered pair of visits about 6 months apart (`--epoch-strategy first-last` picks the earliest and latest instead).
3. **Downloads cutouts, not full files.** It uses IRSA's cutout service (`access_url?center=..&size=..`), which returns the same MEF layout with ZODI and WCS-WAVE included. That's about 0.3 MB in about 12 s, versus 66 MB in about 2 min for a full frame. Each cutout is checked to give the same wavelength as the full frame.
4. **Cleans each exposure.** It computes `IMAGE − ZODI` and masks artifact-flagged pixels. Then it evaluates the spectral WCS at the target and measures aperture photometry. Finally it applies `Cutout2D` and `reproject_interp` onto a north-up grid at native resolution, centred on the target's epoch-1 position.
5. **Corrects for proper motion.** The target's position is propagated to each exposure's date, using the astrometry in `app/core/targets.py`. Without this, Barnard's Star and WISE 0855 would land several arcminutes away from the centre of a 5′ field.
6. **Builds the products.**
   - RGB image: B = detectors 1–2, G = 3–4, R = 5–6.
   - H₂O (1.4–1.9 and 2.6–2.8 µm) and CO₂ (4.2–4.4 µm) filter images.
   - Difference image (epoch 2 − epoch 1).
   - Anomalies found with OpenCV on the difference image's SNR map.
   - A 6 × 17 = 102-channel LVF spectrum, combined with inverse-variance weighting.
   - A light curve, averaged over the channels both epochs share.

   Both epochs use the same display stretch, so a change in brightness shows up in the images.

### Things the frontend should know

- `pixel_x` and `pixel_y` are coordinates in the **PNG** (default 600 × 600, origin top-left). An anomaly marker can be drawn directly on `subtracted_image_url`.
- `flux_mjy_sr` is the average surface brightness in the aperture, in MJy/sr, after the local background is subtracted.
- `contamination_flags` uses SPHEREx's own flag names, such as `SOURCE`, `HOT`, `GHOST` and `PERSIST`. A flag is listed when at least half of the exposures covering that pixel have it.
- `chemical_filters` leaves out a molecule when an epoch has no exposures in its wavelength range.

## Adding a target

Add a `Target(...)` to `app/core/targets.py`, then run the pipeline for it. The API serves every catalogue target that has a JSON file, and checks each file against the schema at startup.
