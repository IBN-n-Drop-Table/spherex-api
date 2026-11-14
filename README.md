# spherex-api

Backend for the **SPHEREx** citizen-science app (NASA Space Apps 2026). It serves real data from NASA's SPHEREx space telescope (via IRSA/IPAC): timelapse across survey visits, difference imaging with anomaly detection, 102-band spectra and light curves, plus a feed of citizen discovery reports.

- **Golden Path:** 5 pre-processed targets served instantly from local JSON.
- **Live mode:** any other position is processed from NASA/IRSA as a background job with progress and ETA (optional, needs `requirements-science.txt`).
- **Offline-first:** if IRSA takes longer than 5 s or answers 429/5xx, the nearest cached data is served with a visible warning, never a 500.
- **Citizen reports:** stored in Supabase when configured, in memory otherwise.

## Contents

- [TLDR](#tldr)
- [Run locally](#run-locally)
- [Tests](#tests)
- [Endpoints](#endpoints)
- [Offline-first fallback](#offline-first-fallback)
- [Citizen discovery reports](#citizen-discovery-reports)
- [Configuration](#configuration)
- [Supabase](#supabase)
- [Repository layout](#repository-layout)
- [Golden Path pipeline](#golden-path-pipeline-offline)
- [Notes for frontend developers](#notes-for-frontend-developers)
- [Adding a target](#adding-a-target)
- [What the data supports](#what-the-data-supports-object-by-object)
- [Data used](#data-used)
- [License](#license)

---

## TLDR
This api serves already processed SphereX images from a given right ascension, declination, field of view and band.
It has some preprocessed targets required by Nasa Space Apps to ensure a fast response for these targets, 
which are Barnard’s Star, Epsilon Indi B, WISE 0855, RW Aurigae and R Draconis. 
If the target coordinates do not match the ones pre-cached, a job process is run to create the new spectrogram for those
coordinates.


## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-science.txt 
uvicorn app.main:app --reload --port 8000
```
Once running you can access the api docs and the api healthcheck at:

- Interactive docs (OpenAPI): <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health> (reports `live_processing` and `reports_backend`)

`/health` shows `"live_processing": "available"` when it is ready. To share the API with frontend developers on the same network, use `--host 0.0.0.0`.


## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

---

## Endpoints

Full, always up-to-date schema: **`/docs`**. A real response is in [`docs/contract-example.json`](docs/contract-example.json). CORS is open (`*`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/targets` | The 5 challenge objects (demo buttons) and whether they are cached |
| `GET` | `/api/v1/sky-patch` | Timelapse (every visit), difference image, anomalies, spectrum, light curve, needs specific coordinates or target |
| `POST` | `/api/v1/jobs` | Start (or reuse) a computation; returns a `job_id` at once |
| `GET` | `/api/v1/jobs/{job_id}` | Poll: `status`, `stage`, `progress`, `message`, `eta_s`, `result` |
| `GET` | `/api/v1/epochs` | Visits covering a position (metadata only, no downloads) |
| `GET` | `/api/v1/spectrum` | 102-band spectrum of a target, an anomaly or a position |
| `GET`/`POST` | `/api/v1/reports` | Community feed |
| `POST` | `/api/v1/reports/{id}/verify` | Peer confirmation |
| `GET` | `/credits` (also `/api/v1/credits`) | Data used: SPHEREx DR1/QR3 (DOIs), Gaia DR3, WISE citations and the SPHEREx acknowledgement |
| `GET` | `/health` | Service status |

### Errors

Every error, from every endpoint, has one shape:

```json
{"detail": "fov: Input should be less than or equal to 30", "code": "validation_error",
 "errors": [{"loc": ["query", "fov"], "msg": "Input should be less than or equal to 30", "type": "less_than_equal"}]}
```

- `detail` is always a readable string. (Before 2026-11-14, FastAPI's own 422s put a list there.)
- `code` is stable: `not_found` (404), `job_not_found` (404, a job lost to a restart or expired; see [Jobs](#jobs-with-progress-when-coordinates-are-not-in-the-cache)), `validation_error` (422), `service_unavailable` (503, with `Retry-After`), `method_not_allowed` (405), `internal_error` (500), or `http_<status>`.
- `errors` lists the field problems of a 422 validation error, and is `null` otherwise.

### Coordinates, field of view, band

- `ra` / `dec`: decimal degrees **or** sexagesimal: `ra=08h55m10.831s` (also `08:55:10.831`), `dec=-7d14m42.53s` (also `-07:14:42.53`). Bad input returns **422**.
- `fov`: **arcminutes**, 1 to 30, default 5. Cached targets are always 5'; live jobs are capped at `LIVE_MAX_FOV_ARCMIN` (15).
- `band=H2O|CO2|CO`: only echoed back as `band`. Every response always carries all chemical filters (`chemical_filters`), and the frontend picks one.

### `GET /api/v1/sky-patch`

Every type of query you can make to the sky-patch endpoint.
```bash
curl "http://localhost:8000/api/v1/sky-patch?target=WISE_0855"
curl "http://localhost:8000/api/v1/sky-patch?ra=133.795&dec=-7.245&fov=5"
curl "http://localhost:8000/api/v1/sky-patch?ra=08h55m10.831s&dec=-7d14m42.53s&band=H2O"
curl -i "http://localhost:8000/api/v1/sky-patch?target=M31"          # 404
curl -i "http://localhost:8000/api/v1/sky-patch?ra=10.68&dec=41.27"  # 202 + job
```

Depending on the target or coordinates passed to these endpoints it can have different results.

| Situation | Response |
|---|---|
| `target` is cached, or (`ra`, `dec`) falls in a cached target's box | **200** `SkyPatchResponse`, instantly |
| A catalogue target without cached data, or any other position | **202** job (poll it) |
| Unknown `target` and no coordinates | **404** |
| Value out of range (`dec=200`, `fov=0.5`, `ra=25h`) | **422** |

What the frontend gets as a response from this endpoint (all image URLs absolute):

- `timelapse_epochs[]`: **one entry per SPHEREx visit** (3 for Barnard's Star and WISE 0855), for the blink comparator. Same stretch in every epoch, so blinking is honest. `chemical_filters` has `H2O`, `CO2`, `CO` when that visit has exposures in the window.
- `difference_imaging`: `epoch_a` (last visit) minus `epoch_b` (first visit): the longest baseline, so the most motion (`kind: "first_vs_last"`). `detected_anomalies[]` come with `kind` (`appeared`/`disappeared`), `contamination_flags`, `is_clean_detection`, and the labels `detection_label` ("Detección Asistida por Software (BETA)") and `detection_notice` ("requiere verificación humana").
- `difference_imaging.pairwise[]`: one difference per pair of **consecutive** visits, for the blink comparator. See [Epoch differences](#epoch-differences-first-vs-last-and-consecutive).
- `spectrum_102_bands` (from `spectrum_epoch_id`) and `light_curve` (one point per visit). Where there is no valid photometry, `flux_mjy_sr` is `null` with a `note`, never 0.
- `saturated` / `spectrum_message`: bright stars (Barnard's Star, RW Aurigae, R Draconis) saturate SPHEREx. Their cores are **shown** in the PNGs but **excluded** from the spectrum and light curve. Such responses also get `quality: "low"`, `quality: "low"` on every spectral band, and a lower-limit `note` on light-curve points (see [What the data supports](#what-the-data-supports-object-by-object)).
- `science_note` / `quality`: what can and cannot be claimed from this object, added when served.
- `fallback` / `fallback_message`: see [Offline-first fallback](#offline-first-fallback).

### Jobs with progress (when coordinates are not in the cache)

```bash
curl -X POST localhost:8000/api/v1/jobs -H 'content-type: application/json' \
     -d '{"ra": "00h42m44s", "dec": "+41d16m09s", "fov": 5}'
# 202 {"job_id": "...", "status": "queued", "stage": "queued", "progress": 0, "retry_after_ms": 1500, ...}

curl localhost:8000/api/v1/jobs/<job_id>
# {"status": "running", "stage": "download", "progress": 0.41, "eta_s": 27, ...}
```

- Cached targets come back **200** with `status: completed` and the `result` at once.
- Poll every `retry_after_ms` (1500 ms). Stages: `queued` -> `query` -> `download` -> `stack` -> `render` -> `difference` (only with 3+ visits) -> `done` (or `failed`).
- A live result has the same `pairwise` differences as the Golden Path, built from the same stacks: no extra downloads.
- **A cold job takes tens of seconds to a few minutes**: each IRSA cutout takes ~12 s (it always carries a ~4 MB PSF table), and live mode downloads 1 cutout per detector and visit (~18). Measured on 2026-11-14: 40-100 s for Barnard's Star. The messages say so.
- The same query returns the same job. One job runs at a time; more than 8 queued -> **503** `Retry-After: 30`.
- Jobs live in memory (1 h after finishing). A restart forgets them, which is why the service runs 1 uvicorn worker.
- Polling a job that no longer exists (a restart, e.g. Render spinning the free instance down, or 1 h after it finished) answers **404** with `code: "job_not_found"` and `detail: "El análisis se perdió por un reinicio del servidor; vuelve a lanzarlo."`. Show the message and offer to POST the same query again.
- The legacy `GET /sky-patch` / `GET /spectrum` 202 + `Location` flow still works.

See [Offline-first fallback](#offline-first-fallback) for what a job returns when IRSA fails.

### `GET /api/v1/epochs?ra=..&dec=..`

Visit list (`epoch_id`, `date`, `date_start`, `date_end`, `n_images`, `n_per_detector`). Instant for cached targets; otherwise it makes one IRSA query (at most 5 s) with the same fallback. Use it to draw the timeline while a job runs.

### `GET /api/v1/spectrum`

Precomputed 102-band spectra, so clicking an anomaly needs no live processing.

- `?target=WISE_0855`: the target (photometry at the patch centre).
- `?target=WISE_0855&anomaly_id=anom_04`: one detected anomaly (404 if it doesn't exist).
- `?ra=..&dec=..[&radius_arcsec=6]`: the closest cached target centre or anomaly within the radius.

Anything else returns **202** with a live job.

---

## Offline-first fallback

IRSA gets **5 s** (`SIA_BUDGET_S`, retries included) to answer. On a timeout, a 429 or 5xx, or when the server has no science stack, the job still **completes**: its result is the cached patch nearest to the requested position, with

```json
"fallback": true,
"fallback_message": "Mostrando datos en caché debido a alta latencia en servidores de origen. ...",
"fallback_reason": "irsa_unavailable"
```

`fallback_reason` is the machine-readable cause, on sky patches, spectra and `/epochs`; it is `null` when `fallback` is false. Branch on it, show `fallback_message` (Spanish), which is worded for that cause:

| `fallback_reason` | When | `fallback_message` starts with |
|---|---|---|
| `irsa_unavailable` | IRSA timed out, throttled (429) or answered 5xx | "Mostrando datos en caché debido a alta latencia en servidores de origen" (+ the nearest object if it is far, + the technical detail) |
| `live_processing_unavailable` | The position is not pre-computed and this server has no live processing (no science stack) | "Estas coordenadas no están precalculadas y el análisis en vivo no está disponible ahora en este servidor. Elige uno de los 5 objetos del reto o vuelve a lanzar un análisis en vivo (POST /api/v1/jobs) más tarde" + which object is shown instead |
| `unknown` | Anything else that still fell back | "Mostrando datos en caché: el análisis en vivo no pudo completarse" |

`target_info.name` says which cached object is shown. A failure caused by IRSA never produces a 500. A position IRSA answers for but SPHEREx hasn't covered twice yet ends as `status: failed` with a readable `error`.

---

## Citizen discovery reports

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/reports` | Submit a report. Returns 201 with the stored report |
| `GET` | `/api/v1/reports` | Feed, newest first. Optional `target_name`, `classification`, `limit` (1-100, default 20) |
| `POST` | `/api/v1/reports/{id}/verify` | Peer confirmation: `verifications_count + 1`. 404 for an unknown id |

```bash
curl -X POST localhost:8000/api/v1/reports -H 'content-type: application/json' -d '{
  "discoverer": "stargazer_ana", "ra_deg": 133.758282, "dec_deg": -7.241336,
  "target_name": "WISE_0855", "pixel_x": 312, "pixel_y": 293,
  "classification": "brown_dwarf", "anomaly_id": "anom_09"}'
curl "localhost:8000/api/v1/reports?target_name=WISE_0855&limit=10"
curl -X POST localhost:8000/api/v1/reports/<id>/verify
```

`classification` is one of `asteroid`, `brown_dwarf`, `high_proper_motion_star`, `variable_star`, `transient_anomaly`, `other`. `pixel_x` / `pixel_y` must be sent together. Catalogue target names are stored in a single spelling (`wise_0855` becomes `WISE 0855`), so the feed filter accepts the same spellings as `/sky-patch`.

### Storage: Supabase or in-memory fallback

`GET /health` shows the store in use (`"reports_backend": "supabase"` or `"memory"`).
If the variables are unset, the table is missing or Supabase can't be reached, the API logs a warning and uses an in-memory store. That store starts **empty**. With `REPORTS_DEMO_SEED=1` it starts with 2 demo examples (WISE 0855 and Barnard's Star) by `ejemplo_demo`, flagged `is_example: true` and prefixed `[EJEMPLO]`: label them in the UI, they are not real citizen reports. Supabase rows always have `is_example: false`.
If Supabase fails during the demo, the API switches to memory and stays there until restart, so the feed doesn't jump between two stores. Reports kept in memory are lost on restart.

---

## Configuration

Copy `.env.example` to `.env`. Every option can be overridden with an environment variable.

| Variable | Default | Description |
|---|---|---|
| `CORS_ORIGINS` | `*` | Allowed origins (comma-separated) |
| `PUBLIC_BASE` | request's base URL | Public server origin used to build absolute image URLs. A trailing `/static` is ignored |
| `SUPABASE_URL` / `SUPABASE_KEY` | - | Enable Supabase for reports (the anon key is enough; RLS policies apply) |
| `SUPABASE_SERVICE_ROLE_KEY` | - | Only for `setup_supabase.py` (takes priority over `SUPABASE_KEY`) |
| `SUPABASE_BUCKET` | `spherex-golden-path` | Public bucket for the PNG mirror |
| `SUPABASE_DB_URL` | - | Lets the setup script apply the SQL migration |
| `SUPABASE_TIMEOUT_S` | 5 | Supabase timeout (fails fast, then degrades to memory) |
| `SIA_BUDGET_S` | 5.0 | Max time (retries included) for the IRSA query before serving cache |
| `LIVE_MAX_PER_DETECTOR` | 1 | Cutouts per detector and visit in live mode |
| `LIVE_DOWNLOAD_WORKERS` | 4 | Parallel downloads in live mode |
| `LIVE_MAX_FOV_ARCMIN` | 15.0 | Max FOV in live mode |
| `LIVE_MAX_RUNNING_JOBS` | 1 | Jobs running at once |
| `LIVE_MAX_ACTIVE_JOBS` | 8 | Queued + running jobs before answering 503 |
| `JOB_TTL_S` | 3600 | Lifetime of a finished job |
| `JOB_RETRY_AFTER_MS` | 1500 | Suggested polling interval |
| `CUTOUT_SECONDS_ESTIMATE` | 12.0 | Seconds per cutout, used only for the displayed ETA |
| `LIVE_WARMUP` | 1 | `0` disables pre-loading the science stack at startup (saves ~100 MB of RAM) |
| `REPORTS_DEMO_SEED` | - | `1`: the in-memory feed starts with 2 demo examples flagged `is_example: true` |

To run the API reading the `.env`:

```bash
uvicorn app.main:app --reload --port 8000 --env-file .env
```

The repo ships a `Dockerfile`, `.dockerignore` and `render.yaml` for deployment (e.g. on Render). Set `PUBLIC_BASE` to the server's public URL.

After deploying, run the smoke test against the public URL (standard library only, nothing to install):

```bash
python scripts/smoke_test.py https://<your-service>.onrender.com
python scripts/smoke_test.py https://<your-service>.onrender.com --skip-live   # without the ~1 min live job
```

It checks `/health`, `/sky-patch` and `/spectrum` for the 5 objects (with `data_credits`), `/credits`, that every image URL and the job `Location` use the scheme and host of the URL you gave (https on a public deploy), CORS on a PNG and on a preflight, the error format, `job_not_found`, and a live `POST /jobs` (fov 2′) polled until it completes. It prints `OK`/`FALLO` per check and exits 1 if anything failed. The first request waits up to 2 min for a sleeping free instance to wake up.

---

## Supabase

[`scripts/setup_supabase.py`](scripts/setup_supabase.py) prepares Supabase and publishes the PNGs already generated in `app/static/golden_path/`. It does **not** re-run the FITS pipeline, and it is safe to run again.

```bash
cp .env.example .env        # fill in SUPABASE_URL, the keys and SUPABASE_BUCKET
.venv/bin/python scripts/setup_supabase.py --dry-run   # validate + show the plan, no network
.venv/bin/python scripts/setup_supabase.py
.venv/bin/uvicorn app.main:app --reload --port 8000 --env-file .env
```

1. **Database.** If `SUPABASE_DB_URL` is set and `psycopg` is installed (`pip install "psycopg[binary]"`), it applies [`supabase/migrations/20261114000000_citizen_reports.sql`](supabase/migrations/20261114000000_citizen_reports.sql). If not, it checks that `citizen_reports` is reachable; if the table is missing, it prints how to paste the SQL once into **Dashboard -> SQL Editor**.
2. **Storage.** It creates the public bucket (or makes an existing one public), then uploads every PNG to `golden_path/<file>`, overwriting existing files.
3. **JSON (only with `--rewrite-json`).** By default the bucket is only a **mirror**: `app/data/*.json` keep image paths relative to the API (`/static/golden_path/...`), the API serves the images itself, and the demo keeps working even if Supabase is down. With `--rewrite-json`, the JSON files point at the bucket instead.

Keys: the script needs the **service_role** key (it creates the bucket and uploads); the API only needs the **anon** key in `SUPABASE_KEY`, so one `.env` works for both. Exit codes: `0` ready, `1` a step failed, `2` credentials missing. To go back to local images after `--rewrite-json`, re-run `generate_golden_path.py`.

---

## Repository layout

```
app/
  main.py                 # FastAPI app, CORS, router wiring, startup cache warm-up
  core/
    config.py             # settings (environment variables)
    targets.py            # the 5 targets: coordinates, astrometry, lookup boxes
    patch_geometry.py     # patch WCS, pixel conventions, duplicate suppression (pure Python)
    coords.py             # RA/Dec parser: degrees or sexagesimal (pure Python)
    urls.py               # relative image paths on disk <-> absolute URLs on the wire
  schemas/
    sky_patch.py          # response contract (Pydantic)
    reports.py            # citizen report contract
  routers/
    sky_patch.py          # /sky-patch
    spectrum.py           # /spectrum
    jobs.py               # POST /jobs, GET /jobs/{id}
    epochs.py             # /epochs and /targets
    reports.py            # /reports
    common.py             # coordinates, public URLs, live jobs + fallback
  services/
    golden_path.py        # lookup over cached targets and spectra
    fallback.py           # nearest cached patch when IRSA fails
    jobs.py               # thread-safe in-memory job store (TTL, de-dup, limits)
    live.py               # runs the pipeline for uncached positions
    reports.py            # Supabase repo + in-memory fallback
  data/<target>.json      # generated Golden Path payloads
  static/golden_path/     # generated PNGs (--storage local), committed to the repo
scripts/
  generate_golden_path.py     # offline IRSA -> JSON/PNG pipeline
  setup_supabase.py           # migration + bucket + optional PNG mirror
  export_contract_example.py  # writes docs/contract-example.json
  smoke_test.py               # checks a deployed API (OK/FALLO per check)
supabase/migrations/      # SQL for citizen_reports (table, indexes, RLS, verify RPC)
docs/                     # contract-example.json
notebooks/
tests/
Dockerfile · render.yaml · .env.example
requirements.txt · requirements-science.txt · requirements-dev.txt
```

---

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

With `--storage local` (the default), PNGs go to `app/static/golden_path/` and are **committed to the repo**. The JSON stores relative paths and the API prepends its public base (`PUBLIC_BASE`, or the request host). With `--storage supabase` the pipeline uploads straight to the bucket (it must already exist and be public; needs `SUPABASE_URL`, a `SUPABASE_KEY` with Storage write access, and `SUPABASE_BUCKET`).

```bash
export SUPABASE_URL="https://<project>.supabase.co"
export SUPABASE_KEY="<service-role key>"   # needs write access to Storage
export SUPABASE_BUCKET="spherex-golden-path"
python scripts/generate_golden_path.py --all --storage supabase
```

Downloaded cutouts are stored in astropy's download cache (`~/.cache/astropy` with astropy 7+, `~/.astropy/cache` before), so a re-run only reprocesses them and downloads only what is missing; it logs `Cutouts: N/M already in the astropy cache`. SIA answers are kept in `cache/sia/` (git-ignored). To download everything again, pass `--no-cache`. For all options, run `--help`.

```bash
# Cache only, no network at all (fails with a clear message if a SIA answer
# or a cutout is missing). Same as CACHE_ONLY=1.
python scripts/generate_golden_path.py --all --offline
# ...or leave uncached cutouts out instead of failing (what a failed download does)
python scripts/generate_golden_path.py --all --offline --skip-missing
# Write JSON + PNGs to a staging directory instead of the repo, to compare first
python scripts/generate_golden_path.py --all --offline --skip-missing --output-root /tmp/stage
```

With every cutout cached, `--all` takes about 70 s (measured 2026-11-14, 1,944 cutouts).

### What it does

1. **Finds the exposures.** It runs an IRSA SIA2 query on `spherex_dr1` and `spherex_qr3` directly over HTTP, with a time budget and retries (`--sia-budget-s`). It doesn't use `Irsa.query_sia`, whose `/SIA/capabilities` pre-flight was failing with 502 on 2026-11-14. If those collections have too few exposures, it also queries the deep fields. Duplicates across releases are removed.
2. **Uses every visit.** Exposures are grouped into survey visits by gaps in `t_min` (`--epoch-gap-days`, 30), never by fixed calendar semesters, which split a visit in two. Every visit becomes a timelapse epoch; the difference image compares the **first and the last** visit.
3. **Downloads cutouts, not full files.** It uses IRSA's cutout service, which returns the same MEF layout (IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE). A 5' cutout is ~4 MB and takes ~11-13 s because IRSA always includes the full EPSF table; a full frame is ~70 MB. This download is the bottleneck, and its cost scales with the number of cutouts, not with the FOV.
4. **Cleans each exposure.** It computes `IMAGE - ZODI` and masks artifact-flagged pixels. Saturated pixels (`OVERFLOW`/`BLOOM`) are kept in a separate **display-only** copy: the PNGs show bright stars, while photometry, spectra, light curves and detection never use them. It evaluates the spectral WCS at the target, measures aperture photometry and reprojects onto a north-up, square-pixel TAN grid centred on the target's epoch-1 position (50 x 50 px of 6.0" for 5'; the 600 px PNGs are an exact 12x upscale).
5. **Corrects for proper motion.** The target's position is propagated to each exposure's date using the astrometry in `app/core/targets.py`. Without this, Barnard's Star and WISE 0855 would land several arcminutes from the centre of a 5' field.
6. **Builds the products.**
   - RGB image: B = detectors 1–2, G = 3–4, R = 5–6.
   - H₂O (1.4–1.9 and 2.6–2.8 µm), CO₂ (4.2–4.4 µm) and CO (4.60–4.74 µm) filter images.
   - Difference image (last − first visit). Each detector's coadd is only ever subtracted from the same detector's coadd, because each detector sees different wavelengths.
   - Consecutive-visit differences (`pairwise`), from the same per-visit stacks: no re-stacking, no anomaly detection.
   - Anomalies found with OpenCV on the (first vs last) difference image's SNR map. Detections within `--dedup-radius-px` (12) PNG px are merged, and each remaining anomaly gets its own 102-band spectrum.
   - A 6 × 17 = 102-channel LVF spectrum, combined with inverse-variance weighting.
   - A light curve with one point per visit, averaged over the channels every visit shares. No valid photometry gives `null` plus a `note`, never 0.

The API validates every `app/data/*.json` file at startup and refuses to start if one is out of date. Regenerate the files (the astropy cache makes this quick when the cutouts are already downloaded), then publish again if you use Supabase:

```bash
python scripts/generate_golden_path.py --all --workers 2
python scripts/setup_supabase.py      # optional: mirror the PNGs to Supabase
```

---

## Notes for frontend developers

**Patch geometry (`patch`).** Every image in a response (`image_url`, `chemical_filters`, `subtracted_image_url`) shares one geometry, given by `patch.width_px`, `patch.height_px` and `patch.wcs`:

- `patch.wcs` is a FITS WCS (CD-matrix form): north up, east left, square pixels, gnomonic (`RA---TAN`/`DEC--TAN`). FITS pixel numbering: 1-based, +Y up; `crpix = (300.5, 300.5)` is the image centre. `cd1_1 = -(fov_arcmin/60)/width_px`, `cd2_2 = +(fov_arcmin/60)/height_px`.
- Aladin Lite v3 can take these keys directly, together with `NAXIS1 = width_px` and `NAXIS2 = height_px`.
- `crval1/crval2` is the **patch centre**: the target's position at the epoch-1 date. For fast-moving stars this is **not** `target_info.ra_deg/dec_deg` (the catalogue position): WISE 0855's 2010 position is about 2' east of where it was in 2025. Centre the view on `crval`.

**Pixels.** `pixel_x` and `pixel_y` are **0-based PNG indices, origin top-left** (+Y down). To convert to FITS: `x_fits = pixel_x + 1`, `y_fits = height_px - pixel_y`. An anomaly marker can be drawn straight on the PNG, or placed on the sky with `ra_deg`/`dec_deg`; the two agree to within 0.5".

**Formats.** `image_format` (`"png"` or `"fits"`) is set on each timelapse epoch (it also applies to that epoch's `chemical_filters`) and on `difference_imaging`. Everything cached is `png` today.

**Difference image.** `subtracted = epoch_a - epoch_b` = last visit - first visit. Red means brighter in `epoch_a`, blue brighter in `epoch_b`.

### Epoch differences: first vs last, and consecutive

`difference_imaging` holds two kinds of difference image. Both are `later − earlier` (red = brighter in the later visit, blue = fainter, black = no data) and share the patch geometry.

| | `difference_imaging` itself | each item of `difference_imaging.pairwise[]` |
|---|---|---|
| `kind` | `"first_vs_last"` | `"consecutive"` |
| visits | first and last | visit *i* and *i + 1*, in time order |
| detectors | every detector both visits share | detectors both visits share with ≥ 5 frames in each (a median of 3 frames doesn't reject artefacts); if none qualifies, every shared one |
| colour scale | 99.5th percentile of \|difference\| (unchanged) | ± 5 σ of the background, saturated/masked pixels excluded, after removing a smooth sky (60″ blocks, sources masked) |
| anomaly detection | yes (`detected_anomalies`) | **no**: fixed stars leave 6–14 % residuals that would show up as false anomalies |

Fields on both: `epoch_a` (later), `epoch_b` (earlier), `delta_t_days`, `detectors_used`, `expected_shift_arcsec` (catalogue proper motion × Δt) and `weak_signal`. `weak_signal: true` means that expected shift is below one native SPHEREx pixel (6.15″): a dipole, if any, is faint, and the UI should say so. Every consecutive pair of the 5 challenge targets is `weak_signal: true` (2–6″ in ~6 months); only first-vs-last reaches 6.6–10.2″ for the fast movers. `pairwise` is `[]` with fewer than 3 visits, because the only pair is then the first-vs-last one.

**Picking the image for the blink comparator.** For the two epochs on screen, look in `pairwise` for the item whose `{epoch_a, epoch_b}` is that pair. If there isn't one, and the pair is the first and last visit, use `difference_imaging` itself. Any other pair (e.g. visit 1 vs visit 3 of 4) has no image: offer the closest consecutive pair or first-vs-last.

```js
function diffFor(d, e1, e2) {
  const want = new Set([e1, e2]);
  const hit = (d.pairwise ?? []).find(p => want.has(p.epoch_a) && want.has(p.epoch_b));
  if (hit) return hit;
  if (want.has(d.epoch_a) && want.has(d.epoch_b)) return d; // first_vs_last
  return null;
}
```

Read the pair images knowing what the targets are: Barnard's Star and Epsilon Indi B show a clear dipole in every pair; WISE 0855 is lost next to a field star in these broadband differences; RW Aurigae and R Draconis have no motion, and what changes is their (saturated) brightness.

**Anomalies.**
- Every detection has its own `spectrum_102_bands`, measured in `spectrum_epoch_id`: `epoch_a` if the source brightened, `epoch_b` if it faded.
- Detections within 12 PNG px of each other are merged; the highest SNR is kept.
- `anomaly_id` values are renumbered whenever the pipeline re-runs, so don't store them long-term.

**Other fields.**
- `flux_mjy_sr` is the average surface brightness in the aperture, in MJy/sr, after the local background is subtracted.
- `contamination_flags` uses SPHEREx's own flag names (`SOURCE`, `HOT`, `GHOST`, `PERSIST`, ...); a flag is listed when at least half of the exposures covering that pixel have it.
- `chemical_filters` leaves out a molecule when an epoch has no exposures in its wavelength range.

**CORS.** The API, the `/static/...` PNGs (served with `Access-Control-Allow-Origin: *` even without an `Origin` header) and Supabase public URLs can all be read from WebGL.

---

## Adding a target

Add a `Target(...)` to `app/core/targets.py`, then run the pipeline for it. The API serves every catalogue target that has a JSON file and checks each file against the schema at startup.

Current targets: `WISE_0855`, `BARNARDS_STAR`, `EPSILON_INDI_B`, `RW_AURIGAE`, `R_DRACONIS`. Names are normalised (`wise 0855`, `WISE-0855` and `WISE_0855` are equivalent).

---

## What the data supports, object by object

Some caveats are added to responses **when they are served**, from a static table (`app/core/science_notes.py`); the pre-processed files are never edited. Fields: `science_note` (Spanish, for the UI), `quality` (`unvalidated` or `low`) on `/sky-patch` and on target spectra from `/spectrum`, and a `note` on light-curve points.

**WISE 0855** (`quality: "unvalidated"`). Measured on 2026-11-14 (60 exposures per visit):

| You can say | You cannot say |
|---|---|
| The patch follows its proper motion: it is centred on its position at the first visit, ~2′ west of the 2010 catalogue position. | That the broadband spectrum or light curve is WISE 0855's: both are blended with a fixed field star ~11″ away, which dominates at 1 µm. |
| In detector 6 (4.4–5 µm) its centroid moves 7.9″ west between the first and last visit, against 8.05″ expected. | That the software detects it: none of the anomalies falls on it, and the broadband difference image does not show its dipole. |
| | That this is the spectrum of a Y dwarf. |

**Saturated objects** (`saturated: true`: Barnard's Star, RW Aurigae, R Draconis, and any live position that saturates). The rule is applied to every such response: `quality: "low"` on the response and on each spectral band, and every light-curve point that has a flux gets the note *"Límite inferior sesgado ... No sirve para afirmar variabilidad"*: the photometry used only the unsaturated pixels of the aperture.

| Object | You can say | You cannot say |
|---|---|---|
| Barnard's Star | Its proper motion shows in the difference image: 10.3″ north between the first and last visit, against 10.2″ expected. | Anything from its spectrum or light curve: there are none (empty, `null` + `note`). |
| RW Aurigae | That the core saturates. | That it varies: its spectrum (58 bands) and light curve are biased lower limits. |
| R Draconis | Its halo dims between the first and last visit (flux ratio ~0.56), consistent with a Mira at another phase. | Anything about the core: there is no valid spectrum or light curve. |

**Epsilon Indi B** is not saturated and gets no note: its spectrum (methane dip near 3.3 µm, peak at ~4 µm) and its proper-motion dipole (6.2″ measured against 6.6″ expected) are consistent with a T dwarf.

---

## Data used

The same citations are served by `GET /credits` and in the `data_credits` field of every `/sky-patch` and `/spectrum` response (one source: `app/core/credits.py`; a test checks it matches this section word for word).

Everything the API serves comes from public archives. Nothing is simulated: where a measurement is impossible (e.g. saturated stars), the response says so (`null` + `note`, `saturated`, `spectrum_message`).

| Source | What we use it for | Access |
|---|---|---|
| **SPHEREx** (NASA/JPL/Caltech), served by **IRSA/IPAC** | All images, difference images, spectra and light curves: Level 2 Spectral Images (MEF), downloaded as server-side cutouts | SIA2 `https://irsa.ipac.caltech.edu/SIA`, collections `spherex_dr1` ([DOI 10.26131/IRSA678](https://www.ipac.caltech.edu/doi/10.26131/IRSA678)) and `spherex_qr3` ([DOI 10.26131/IRSA662](https://www.ipac.caltech.edu/doi/10.26131/IRSA662)); deep fields `*_deep` only as a fallback. [SPHEREx at IRSA](https://irsa.ipac.caltech.edu/Missions/spherex.html), [archive user guide](https://caltech-ipac.github.io/spherex-archive-documentation/), [tutorials](https://caltech-ipac.github.io/irsa-tutorials/spherex-intro/) |
| **Gaia DR3** (ESA) | Reference positions and proper motions of Barnard's Star, Epsilon Indi B, RW Aurigae and R Draconis, propagated to each SPHEREx exposure date | [Gaia archive](https://gea.esac.esa.int/archive/); Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1 |
| **WISE** (NASA) | Reference position of WISE 0855-0714 (from its WISE designation, epoch ~2010.4); proper motion from Luhman & Esplin (2016, AJ 152, 78) | [WISE at IRSA](https://irsa.ipac.caltech.edu/Missions/wise.html) |

Gaia/WISE catalogue overlays in the viewer are drawn by the frontend (Aladin Lite), not by this API.

SPHEREx acknowledgement, as requested by IRSA ([source](https://irsa.ipac.caltech.edu/Missions/spherex.html)):

> This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration.

Software: FastAPI, Astropy, Astroquery/PyVO, reproject, NumPy, OpenCV, Matplotlib, Pillow.

## Chat IA

`POST /api/v1/chat` explains, in Spanish, what the user sees. `GET /api/v1/chat/suggestions?target=` returns 4 suggested questions. Full contract with examples: [docs/wow-contract.md](docs/wow-contract.md).

- **Providers.** Groq goes first and DeepSeek is the backup. Both use the OpenAI-compatible `/chat/completions` endpoint over async httpx. Each provider gets 8 s, retry included, and at most one retry, only on connection errors, 429 or 5xx.
- **Fallback.** With no keys, with both providers failing, or past the daily cap, the endpoint returns a canned Spanish answer with `fallback: true`. A provider never causes a 5xx.
- **Prompt.** The system prompt is fixed in `app/services/chat.py` and is in Spanish, with `tone` set to `easy` or `technical`. The model may use only the context it is sent. It must say "no lo sé" when the context lacks the answer, never claim discoveries, and remind the user that findings need human verification.
- **Untrusted input.** The client's `context` and `history` are wrapped as untrusted data, and the prompt says they cannot change its rules.
- **Cautions.** The server reads `science_note`, `quality: "low"`, `saturated`, `weak_signal` and `fallback_reason` from the context. It injects them into the prompt and also returns them as `cautions`.
- **Protection.**
  - Per-IP rate limit (in memory, best effort), answered with 429 and `Retry-After`.
  - A global daily cap on provider calls.
  - A 1 h cache for identical question + context + tone + history.
  - Input limits: message ≤ 500 characters, context ≤ 4 KB, history trimmed to the last 6 turns.
- **Keys.** API keys live only in environment variables. They are never logged and never returned; `tests/test_chat.py` checks both.

| Variable | Default | Meaning |
|---|---|---|
| `GROQ_API_KEY` | (unset) | Groq key. Unset: Groq is skipped |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Production model on Groq (checked 2026-11-14; `llama-3.3-70b-versatile` was shut down for free/developer tiers) |
| `DEEPSEEK_API_KEY` | (unset) | DeepSeek key. Unset: DeepSeek is skipped |
| `DEEPSEEK_MODEL` | `deepseek-flash` | DeepSeek's fast model (V4.1 Flash); `deepseek-chat` was discontinued. Sent with thinking disabled |
| `GROQ_BASE_URL` / `DEEPSEEK_BASE_URL` | `https://api.groq.com/openai/v1` / `https://api.deepseek.com` | |
| `CHAT_PROVIDER_TIMEOUT_S` | `8` | Per provider, retry included |
| `CHAT_MAX_TOKENS` | `600` | |
| `CHAT_RATE_PER_MIN` | `8` | Chat requests per IP per minute |
| `CHAT_DAILY_PROVIDER_CAP` | `1000` | Provider calls per UTC day, all users together |
| `CHAT_CACHE_TTL_S` / `CHAT_CACHE_MAX_ENTRIES` | `3600` / `500` | |

## Juego

The game "Encuentra lo que se mueve" is a blink comparator game. The player marks where the object moves between two visits. The server keeps the answer. Endpoints:

- `POST /api/v1/game/sessions`
- `POST /api/v1/game/guess`
- `POST /api/v1/game/finish`
- `GET /api/v1/game/leaderboard`

Contract and examples: [docs/wow-contract.md](docs/wow-contract.md).

- **Rounds.** Rounds use only Barnard's Star and Epsilon Indi B, with their consecutive pairs and the first-vs-last pair. That gives 7 pairs, sorted as easy, medium or hard by the expected shift in pixels. WISE 0855, RW Aurigae and R Draconis are not used, and neither are the detected anomalies.
- **Ground truth.** The object's position at each visit comes from Gaia DR3 proper motion propagated to the visit date and projected with the patch WCS. `tests/test_game.py` checks it against the light centroid in every cached PNG; all agree within 0.7 px (0.35″).
- **Crops.** Patches are centred on the object, so each round shows a 300 px window cut with Pillow. The window puts the object at one of 25 positions. Crops are written once to `app/static/game/` (git-ignored) under salted-hash names, which don't reveal the window. `GAME_SALT` fixes the salt across restarts.
- **Score.** Each round scores 0–1000, computed on the server. A miss, more than 20 px from the object's path, scores 0. A hit scores `500 + 300·(1 − d/20) + 200·max(0, 1 − t/30 s)`. `finish` adds up the stored rounds and ignores anything the client might claim.
- **Protection.**
  - One attempt per round (409).
  - Sessions expire after 15 min idle (410).
  - `x`/`y` must fall inside the image (422).
  - Nicknames are 3–16 characters: letters, digits, `_` and `-`.
  - Per-IP rate limits (429 + `Retry-After`).
- **Leaderboard.** Scores go to the Supabase table `game_scores` when `SUPABASE_URL`/`SUPABASE_KEY` are set and the table exists, otherwise to memory. If Supabase fails mid-demo, the leaderboard stays in memory until restart. Create the table by running [supabase/game_scores.sql](supabase/game_scores.sql) once in Supabase Dashboard → SQL Editor, then restart the API. The file is idempotent and explains its security model.

| Variable | Default | Meaning |
|---|---|---|
| `GAME_SESSION_TTL_S` | `900` | Idle time before a session expires |
| `GAME_MAX_SESSIONS` | `2000` | Active sessions kept in memory |
| `GAME_CROP_PX` | `300` | Side of the round images |
| `GAME_SESSIONS_PER_MIN` / `GAME_GUESSES_PER_MIN` / `GAME_FINISH_PER_MIN` | `10` / `60` / `5` | Per IP |
| `GAME_SALT` | random per process | Salt for crop file names |

`GET /api/v1/targets` also carries distances and plain-language texts for the subtitles: `distance_ly`, `distance_source`, `light_left_years_ago`, `distance_uncertain`, `description_es` and `story_es`. They are derived from cited parallaxes in `app/core/target_stories.py`.

## Loading phrases

`GET /api/v1/loading-phrases` returns light-hearted English phrases for loading screens, grouped as `generic`, `cold_start`, `subtraction`, `game` and `chat`. They live in `app/core/loading_phrases.py`, which imports nothing else from the app, so the endpoint answers even while the database, the AI providers or the cached data are unavailable (for example while a free Render instance wakes up). Responses carry `Cache-Control: public, max-age=3600`. New phrases must not claim anything we cannot back up. Example: [docs/wow-contract.md](docs/wow-contract.md#4-frases-de-carga-get-apiv1loading-phrases).

## License

MIT, see [LICENSE](LICENSE).
