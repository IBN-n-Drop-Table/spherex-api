# spherex-api

Backend for the SPHEREx citizen-science app (NASA Space Apps 2026).
The API serves pre-processed "Golden Path" targets from local JSON, instantly. Other positions are processed live from NASA/IRSA as background jobs (optional, needs `requirements-science.txt`). Citizen discovery reports go to Supabase when it is configured, and to an in-memory store otherwise.

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
curl -i "http://localhost:8000/api/v1/sky-patch?ra=10.68&dec=41.27"   # 202 + job
```

Live jobs run the pipeline inside the API process. To enable them, install `pip install -r requirements-science.txt` in the same venv. `/health` shows `"live_processing": "available"` when it's ready.

To share it with frontend devs on the same network, use `--host 0.0.0.0`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Endpoints

### `GET /api/v1/sky-patch`

| param    | type  | notes                                   |
|----------|-------|-----------------------------------------|
| `target` | str   | `WISE_0855` (case, spaces and hyphens are ignored) |
| `ra`     | float | degrees, 0–360                           |
| `dec`    | float | degrees, −90–90                          |
| `fov`    | float | **arcminutes**, 1–30, default 5. Cached targets are always 5′ |

| situation | response |
|-----------|----------|
| `target` is cached, or (`ra`, `dec`) falls in a cached target's box | **200** `SkyPatchResponse` |
| `target` is a catalogue target without cached data, or (`ra`, `dec`) anywhere else | **202** job (below) |
| unknown `target` and no coordinates | **404** `{"detail": "Target not found in Golden Path cache. Try WISE_0855."}` |
| value out of range (e.g. `dec=200`, `fov=0.5`) | **422** |

### `GET /api/v1/spectrum`

Precomputed 102-band spectra, so clicking an anomaly needs no live processing.

- `?target=WISE_0855`: the target (photometry at the patch centre).
- `?target=WISE_0855&anomaly_id=anom_04`: one detected anomaly (404 if it doesn't exist).
- `?ra=..&dec=..[&radius_arcsec=6]`: the closest cached target centre or anomaly within the radius.

Anything else returns **202** with a live job. The same spectra are also embedded in `/sky-patch` (`detected_anomalies[].spectrum_102_bands`).

### Live jobs: `GET /api/v1/jobs/{job_id}`

A 202 answers immediately with `{"job_id": "…", "status": "queued", …}`. Its `Location` header holds the polling URL, and `Retry-After: 5` says how long to wait. Poll until `status` is `completed` (`result` holds the same body a 200 would) or `failed` (`error` holds a readable message, e.g. when IRSA is down).

```json
{"job_id": "…", "kind": "sky_patch", "status": "queued | running | completed | failed",
 "result": null, "error": null, "created_at": "…", "updated_at": "…"}
```

- Repeating the same query while its job is queued or running returns the same job. Once it has completed, the same query answers 200 with the result.
- Jobs are kept in memory for `JOB_TTL_S` (default 1 h) after they finish; then the job and its PNGs (`/static/live/<job_id>/`) are deleted. A restart forgets all jobs.
- To be gentle with IRSA, one job runs at a time (`LIVE_MAX_RUNNING_JOBS`), with up to 24 exposures per epoch (`LIVE_MAX_EXPOSURES_PER_EPOCH`). With more than `LIVE_MAX_ACTIVE_JOBS` (8) queued or running, new queries get **503** with `Retry-After: 30`.
- A live job takes 1–3 minutes when IRSA is healthy.

### Citizen discovery reports (community feed)

| method | path | notes |
|--------|------|-------|
| `POST` | `/api/v1/reports` | Submit a report. Returns 201 with the stored report. |
| `GET`  | `/api/v1/reports` | Feed, newest first. Optional `target_name`, `classification`, `limit` (1–100, default 20). |
| `POST` | `/api/v1/reports/{id}/verify` | Peer confirmation: `verifications_count + 1`. Returns 404 for an unknown id. |

```bash
curl -X POST localhost:8000/api/v1/reports -H 'content-type: application/json' -d '{
  "discoverer": "stargazer_ana", "ra_deg": 133.758282, "dec_deg": -7.241336,
  "target_name": "WISE_0855", "pixel_x": 312, "pixel_y": 293,
  "classification": "brown_dwarf", "anomaly_id": "anom_09"}'
curl "localhost:8000/api/v1/reports?target_name=WISE_0855&limit=10"
curl -X POST localhost:8000/api/v1/reports/<id>/verify
```

`classification` is one of `asteroid`, `brown_dwarf`, `high_proper_motion_star`, `variable_star`, `transient_anomaly`, `other`.
`pixel_x`/`pixel_y` must be sent together. Catalogue target names are stored in a single spelling (`wise_0855` becomes `WISE 0855`), so the feed filter accepts the same spellings as `/sky-patch`.

#### Storage: Supabase, or in-memory fallback

Run [`scripts/setup_supabase.py`](#supabase-setup-one-command), or apply the migration by hand (SQL Editor, below). Then start the API with `--env-file .env`.

`GET /health` shows the store in use (`"reports_backend": "supabase"` or `"memory"`).
If the variables are unset, the table is missing or Supabase can't be reached, the API logs a warning and uses an in-memory store. That store starts with 2 sample discoveries (WISE 0855 and Barnard's Star).
If Supabase fails during the demo, the API switches to memory and stays there until restart, so the feed doesn't jump between two stores. Reports kept in memory are lost on restart.

The database rules: anyone can read and submit reports. Clients cannot set `id`, `created_at` or `verifications_count`, and nobody can update or delete rows through the API. `verify_citizen_report()` increments the counter atomically.
Verification is anonymous, so one person can confirm a report more than once. Fixing that needs user accounts (Supabase Auth plus a `report_verifications` table).

## Supabase setup (one command)

[`scripts/setup_supabase.py`](scripts/setup_supabase.py) prepares Supabase and publishes the PNGs already generated in `app/static/golden_path/`. It does **not** re-run the FITS pipeline, and it is safe to run again.

```bash
cp .env.example .env        # fill in SUPABASE_URL, the keys and SUPABASE_BUCKET
.venv/bin/python scripts/setup_supabase.py --dry-run   # validate + show the plan, no network
.venv/bin/python scripts/setup_supabase.py
.venv/bin/uvicorn app.main:app --reload --port 8000 --env-file .env
```

What it does:

1. **Database.** If `SUPABASE_DB_URL` is set and `psycopg` is installed (`pip install "psycopg[binary]"`), it applies [`supabase/migrations/20261114000000_citizen_reports.sql`](supabase/migrations/20261114000000_citizen_reports.sql). If not, it checks that `citizen_reports` is reachable. If the table isn't there, it prints how to paste the SQL into **Dashboard → SQL Editor** once.
2. **Storage.** It creates the public bucket `SUPABASE_BUCKET` (default `spherex-golden-path`), or makes an existing one public. Then it uploads every PNG to `golden_path/<file>`, overwriting existing files.
3. **JSON.** It points the image URLs in `app/data/*.json` at the public bucket URLs. That covers `image_url`, `chemical_filters` and `subtracted_image_url`, whether they were localhost or an older bucket. It checks each file against `SkyPatchResponse` and writes it only if something changed.

Keys: the script needs the **service_role** key, because it creates the bucket and uploads. Put it in `SUPABASE_SERVICE_ROLE_KEY`; if that is empty, the script uses `SUPABASE_KEY`. The API only needs the **anon** key in `SUPABASE_KEY`, so one `.env` works for both. Exit code 0 means ready, 1 means some step failed, 2 means credentials are missing.

To go back to local images, re-run `generate_golden_path.py`.

## Layout

```
app/
  main.py               # FastAPI app, CORS, router wiring, startup cache warm-up
  core/config.py        # settings (CORS_ORIGINS, SUPABASE_URL/KEY env vars)
  core/targets.py       # the 5 targets: coordinates, astrometry, lookup boxes
  schemas/sky_patch.py  # Pydantic response contract
  schemas/reports.py    # citizen report contract
  core/patch_geometry.py  # patch WCS, pixel conventions, duplicate suppression (pure Python)
  routers/sky_patch.py  # HTTP layer
  routers/spectrum.py   # /spectrum: target + anomaly spectra
  routers/jobs.py       # /jobs/{id}: live job polling
  routers/reports.py    # /reports: submit, feed, verify
  services/golden_path.py  # lookup over cached targets and spectra
  services/jobs.py      # thread-safe in-memory job store (TTL, de-dup, limits)
  services/live.py      # runs the pipeline for uncached positions
  services/reports.py   # Supabase repo + in-memory fallback
  data/<target>.json    # generated Golden Path payloads
  static/golden_path/   # generated PNGs (--storage local)
scripts/generate_golden_path.py  # offline IRSA -> JSON/PNG pipeline
scripts/setup_supabase.py        # migration + bucket + PNG upload + JSON URL patch
supabase/migrations/  # SQL for citizen_reports (table, indexes, RLS, verify RPC)
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

To publish already-generated PNGs, use [`scripts/setup_supabase.py`](#supabase-setup-one-command). To upload straight from the pipeline instead, the bucket must already exist and be **public**:

```bash
export SUPABASE_URL="https://<project>.supabase.co"
export SUPABASE_KEY="<service-role key>"   # needs write access to Storage
export SUPABASE_BUCKET="spherex-golden-path"
python scripts/generate_golden_path.py --all --storage supabase
```

Downloaded cutouts are stored in astropy's download cache (`~/.astropy/cache`), so a re-run only reprocesses them. To download them again, pass `--no-cache`. For all options, run `--help`.

### What it does

1. **Finds the exposures.** It runs an IRSA SIA query on `spherex_dr1` and `spherex_qr3`. If those have too few exposures, it also queries the deep-field collections. Duplicates across releases are removed.
2. **Picks two epochs.** Exposures are grouped into survey visits by `t_min`. The script picks the best-covered pair of visits about 6 months apart (`--epoch-strategy first-last` picks the earliest and latest instead).
3. **Downloads cutouts, not full files.** It uses IRSA's cutout service (`access_url?center=..&size=..`), which returns the same MEF layout with ZODI and WCS-WAVE included. That's about 0.3 MB in about 12 s, versus 66 MB in about 2 min for a full frame. Each cutout is checked to give the same wavelength as the full frame.
4. **Cleans each exposure.** It computes `IMAGE − ZODI` and masks artifact-flagged pixels. Then it evaluates the spectral WCS at the target and measures aperture photometry. Finally it applies `Cutout2D` and `reproject_interp` onto a north-up, square-pixel TAN grid centred on the target's epoch-1 position. The grid covers exactly `--fov` (50 × 50 px of 6.0″ for 5′, close to SPHEREx's native 6.15″). The 600 px PNGs are an exact 12× upscale of it, which is what `patch.wcs` describes. It also records the LVF wavelength at every grid pixel, so anomalies get spectra at their own position.
5. **Corrects for proper motion.** The target's position is propagated to each exposure's date, using the astrometry in `app/core/targets.py`. Without this, Barnard's Star and WISE 0855 would land several arcminutes away from the centre of a 5′ field.
6. **Builds the products.**
   - RGB image: B = detectors 1–2, G = 3–4, R = 5–6.
   - H₂O (1.4–1.9 and 2.6–2.8 µm) and CO₂ (4.2–4.4 µm) filter images.
   - Difference image (epoch 2 − epoch 1).
   - Anomalies found with OpenCV on the difference image's SNR map. Detections within `--dedup-radius-px` (12) PNG px are merged, and each remaining anomaly gets its own 102-band spectrum.
   - A 6 × 17 = 102-channel LVF spectrum, combined with inverse-variance weighting.
   - A light curve, averaged over the channels both epochs share.

   Both epochs use the same display stretch, so a change in brightness shows up in the images.

### Things the frontend should know

**Patch geometry (`patch`).** Every image in a response (`image_url`, `chemical_filters`, `subtracted_image_url`) shares one geometry, given by `patch.width_px`, `patch.height_px` and `patch.wcs`:

- `patch.wcs` is a FITS WCS (CD-matrix form): north up, east left, square pixels, gnomonic (`RA---TAN`/`DEC--TAN`). It uses FITS pixel numbering: 1-based, +Y up, `crpix = (300.5, 300.5)` is the image centre. `cd1_1 = -(fov_arcmin/60)/width_px`, `cd2_2 = +(fov_arcmin/60)/height_px`.
- Aladin Lite v3 can take these keys directly, together with `NAXIS1 = width_px` and `NAXIS2 = height_px`.
- `crval1/crval2` is the **patch centre**: the target's position at the epoch-1 date. For fast-moving stars this is **not** `target_info.ra_deg/dec_deg`, which is the catalogue position. WISE 0855's 2010 position is about 2′ east of where it was in 2025. Centre the view on `crval`.

**Pixels.** `pixel_x` and `pixel_y` are **0-based PNG indices, origin top-left** (+Y down). To convert to FITS: `x_fits = pixel_x + 1`, `y_fits = height_px − pixel_y`. An anomaly marker can be drawn straight on the PNG, or placed on the sky with `ra_deg`/`dec_deg`; the two agree to within 0.5″.

**Formats.** `image_format` (`"png"` or `"fits"`) is set on each timelapse epoch (it also applies to that epoch's `chemical_filters`) and on `difference_imaging`. Everything cached is `png` today.

**Difference image.** `subtracted = epoch_a − epoch_b` (e.g. `2025_M6 − 2025_M0`). Red means brighter in `epoch_a`, blue brighter in `epoch_b`.

**Anomalies.**
- Every detection has its own `spectrum_102_bands`. It is measured in `spectrum_epoch_id`: `epoch_a` if the source brightened, `epoch_b` if it faded.
- Detections within 12 PNG px of each other are merged; the highest SNR is kept.
- `anomaly_id` values are renumbered whenever the pipeline re-runs, so don't store them long-term.

**Other fields.**
- `flux_mjy_sr` is the average surface brightness in the aperture, in MJy/sr, after the local background is subtracted.
- `contamination_flags` uses SPHEREx's own flag names, such as `SOURCE`, `HOT`, `GHOST` and `PERSIST`. A flag is listed when at least half of the exposures covering that pixel have it.
- `chemical_filters` leaves out a molecule when an epoch has no exposures in its wavelength range.

**CORS.** The API, `/static/...` PNGs (served with `Access-Control-Allow-Origin: *` even without an `Origin` header) and Supabase public URLs can all be read from WebGL.

### After changing the JSON contract

The API validates every `app/data/*.json` file at startup and refuses to start if one is out of date. Regenerate the files (the astropy cache makes this quick when the cutouts are already downloaded), then publish them again if you use Supabase:

```bash
python scripts/generate_golden_path.py --all --workers 2
python scripts/setup_supabase.py      # re-uploads the PNGs and patches the URLs
```

## Adding a target

Add a `Target(...)` to `app/core/targets.py`, then run the pipeline for it. The API serves every catalogue target that has a JSON file, and checks each file against the schema at startup.
