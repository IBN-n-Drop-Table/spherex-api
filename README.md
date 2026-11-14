# spherex-api

Backend for the **SPHEREx** citizen-science app (NASA Space Apps 2026). It serves real data from NASA's SPHEREx space telescope (via IRSA/IPAC): timelapse across survey visits, difference imaging with anomaly detection, 102-band spectra and light curves, plus a feed of citizen discovery reports.

- **Golden Path:** 5 pre-processed targets served instantly from local JSON.
- **Live mode:** any other position is processed from NASA/IRSA as a background job with progress and ETA (optional, needs `requirements-science.txt`).
- **Offline-first:** if IRSA takes longer than 5 s or answers 429/5xx, the nearest cached data is served with a visible warning, never a 500.
- **Citizen reports:** stored in Supabase when configured, in memory otherwise.

## Contents

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
- [Data used](#data-used)
- [License](#license)

---

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

- Interactive docs (OpenAPI): <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health> (reports `live_processing` and `reports_backend`)

```bash
curl "http://localhost:8000/api/v1/sky-patch?target=WISE_0855"
curl "http://localhost:8000/api/v1/sky-patch?ra=133.795&dec=-7.245&fov=5"
curl "http://localhost:8000/api/v1/sky-patch?ra=08h55m10.831s&dec=-7d14m42.53s&band=H2O"
curl -i "http://localhost:8000/api/v1/sky-patch?target=M31"          # 404
curl -i "http://localhost:8000/api/v1/sky-patch?ra=10.68&dec=41.27"  # 202 + job
```

Live jobs run the pipeline inside the API process. To enable them:

```bash
pip install -r requirements-science.txt   # in the same venv
```

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
| `GET` | `/api/v1/sky-patch` | Timelapse (every visit), difference image, anomalies, spectrum, light curve |
| `POST` | `/api/v1/jobs` | Start (or reuse) a computation; returns a `job_id` at once |
| `GET` | `/api/v1/jobs/{job_id}` | Poll: `status`, `stage`, `progress`, `message`, `eta_s`, `result` |
| `GET` | `/api/v1/epochs` | Visits covering a position (metadata only, no downloads) |
| `GET` | `/api/v1/spectrum` | 102-band spectrum of a target, an anomaly or a position |
| `GET`/`POST` | `/api/v1/reports` | Community feed |
| `POST` | `/api/v1/reports/{id}/verify` | Peer confirmation |
| `GET` | `/health` | Service status |

### Coordinates, field of view, band

- `ra` / `dec`: decimal degrees **or** sexagesimal: `ra=08h55m10.831s` (also `08:55:10.831`), `dec=-7d14m42.53s` (also `-07:14:42.53`). Bad input returns **422**.
- `fov`: **arcminutes**, 1 to 30, default 5. Cached targets are always 5'; live jobs are capped at `LIVE_MAX_FOV_ARCMIN` (15).
- `band=H2O|CO2|CO`: only echoed back as `band`. Every response always carries all chemical filters (`chemical_filters`), and the frontend picks one.

### `GET /api/v1/sky-patch`

| Situation | Response |
|---|---|
| `target` is cached, or (`ra`, `dec`) falls in a cached target's box | **200** `SkyPatchResponse`, instantly |
| A catalogue target without cached data, or any other position | **202** job (poll it) |
| Unknown `target` and no coordinates | **404** |
| Value out of range (`dec=200`, `fov=0.5`, `ra=25h`) | **422** |

What the frontend gets (all image URLs absolute):

- `timelapse_epochs[]`: **one entry per SPHEREx visit** (3 for Barnard's Star and WISE 0855), for a blink comparator. Same stretch in every epoch, so blinking is honest. `chemical_filters` has `H2O`, `CO2` and `CO` when that visit has exposures in the window.
- `difference_imaging`: `epoch_a` (last visit) minus `epoch_b` (first visit): the longest baseline, so the most motion. `detected_anomalies[]` come with `kind` (`appeared`/`disappeared`), `contamination_flags`, `is_clean_detection`, and the labels `detection_label` ("Detección Asistida por Software (BETA)") and `detection_notice` ("requiere verificación humana").
- `spectrum_102_bands` (from `spectrum_epoch_id`) and `light_curve` (one point per visit). Where there is no valid photometry, `flux_mjy_sr` is `null` with a `note`, never 0.
- `saturated` / `spectrum_message`: bright stars (Barnard's Star, R Draconis) saturate SPHEREx. Their cores are **shown** in the PNGs but **excluded** from the spectrum and light curve.
- `fallback` / `fallback_message`: see [Offline-first fallback](#offline-first-fallback).

### Jobs with progress (coordinates not in the cache)

```bash
curl -X POST localhost:8000/api/v1/jobs -H 'content-type: application/json' \
     -d '{"ra": "00h42m44s", "dec": "+41d16m09s", "fov": 5}'
# 202 {"job_id": "...", "status": "queued", "stage": "queued", "progress": 0, "retry_after_ms": 1500, ...}

curl localhost:8000/api/v1/jobs/<job_id>
# {"status": "running", "stage": "download", "progress": 0.41, "eta_s": 27, ...}
```

- Cached targets come back **200** with `status: completed` and the `result` at once.
- Poll every `retry_after_ms` (1500 ms). Stages: `queued` -> `query` -> `download` -> `stack` -> `render` -> `done` (or `failed`).
- **A cold job takes tens of seconds to a few minutes:** each IRSA cutout takes ~12 s (it always carries a ~4 MB PSF table), and live mode downloads 1 cutout per detector and visit (~18). Measured on 2026-11-14: 40-100 s for Barnard's Star.
- The same query returns the same job. One job runs at a time; more than 8 queued returns **503** with `Retry-After: 30`.
- Jobs live in memory (1 h after finishing). A restart forgets them, which is why the service runs a single uvicorn worker.
- The legacy `GET /sky-patch` / `GET /spectrum` 202 + `Location` flow still works.

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
"fallback_message": "Mostrando datos en caché debido a alta latencia en servidores de origen. ..."
```

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

`GET /health` shows the store in use (`"reports_backend": "supabase"` or `"memory"`). If the variables are unset, the table is missing or Supabase can't be reached, the API logs a warning and uses an in-memory store that starts with 2 sample discoveries (WISE 0855 and Barnard's Star). If Supabase fails during a demo, the API switches to memory and stays there until restart, so the feed doesn't jump between two stores. Reports kept in memory are lost on restart.

Database rules: anyone can read and submit reports; clients cannot set `id`, `created_at` or `verifications_count`; nobody can update or delete rows through the API. `verify_citizen_report()` increments the counter atomically. Verification is anonymous, so one person can confirm a report more than once. Fixing that needs user accounts (Supabase Auth plus a `report_verifications` table).

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

To run the API reading the `.env`:

```bash
uvicorn app.main:app --reload --port 8000 --env-file .env
```

The repo ships a `Dockerfile`, `.dockerignore` and `render.yaml` for deployment (e.g. on Render). Set `PUBLIC_BASE` to the server's public URL.

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

Downloaded cutouts are kept in astropy's download cache (`~/.astropy/cache`), so a re-run only reprocesses them. To download them again, pass `--no-cache`. For all options run `--help` (`--fov`, `--png-size`, `--workers`, `--epoch-strategy`, `--epoch-gap-days`, `--min-exposures`, `--snr-threshold`, `--max-anomalies`, `--dedup-radius-px`, `--aperture-px`, `--annulus-px`, `--sia-budget-s`, ...).

### What it does

1. **Finds the exposures.** It runs an IRSA SIA2 query on `spherex_dr1` and `spherex_qr3` directly over HTTP, with a time budget and retries (`--sia-budget-s`). It doesn't use `Irsa.query_sia`, whose `/SIA/capabilities` pre-flight was failing with 502 on 2026-11-14. If those collections have too few exposures, it also queries the deep fields. Duplicates across releases are removed.
2. **Uses every visit.** Exposures are grouped into survey visits by gaps in `t_min` (`--epoch-gap-days`, 30), never by fixed calendar semesters, which split a visit in two. Every visit becomes a timelapse epoch; the difference image compares the **first and the last** visit.
3. **Downloads cutouts, not full files.** It uses IRSA's cutout service, which returns the same MEF layout (IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE). A 5' cutout is ~4 MB and takes ~11-13 s because IRSA always includes the full EPSF table; a full frame is ~70 MB. This download is the bottleneck, and its cost scales with the number of cutouts, not with the FOV.
4. **Cleans each exposure.** It computes `IMAGE - ZODI` and masks artifact-flagged pixels. Saturated pixels (`OVERFLOW`/`BLOOM`) are kept in a separate **display-only** copy: the PNGs show bright stars, while photometry, spectra, light curves and detection never use them. It evaluates the spectral WCS at the target, measures aperture photometry and reprojects onto a north-up, square-pixel TAN grid centred on the target's epoch-1 position (50 x 50 px of 6.0" for 5'; the 600 px PNGs are an exact 12x upscale).
5. **Corrects for proper motion.** The target's position is propagated to each exposure's date using the astrometry in `app/core/targets.py`. Without this, Barnard's Star and WISE 0855 would land several arcminutes from the centre of a 5' field.
6. **Builds the products.**
   - RGB image: B = detectors 1-2, G = 3-4, R = 5-6.
   - H2O (1.4-1.9 and 2.6-2.8 um), CO2 (4.2-4.4 um) and CO (4.60-4.74 um) filter images.
   - Difference image (last - first visit), always subtracting each detector from the same detector.
   - Anomalies found with OpenCV on the SNR map; detections within `--dedup-radius-px` (12) are merged and each gets its own 102-band spectrum.
   - A 6 x 17 = 102-channel LVF spectrum, combined with inverse-variance weighting.
   - A light curve with one point per visit; no valid photometry gives `null` plus a `note`, never 0.

   All epochs use the same display stretch, so a brightness change shows up in the images and a saturated core clips to white.

### After changing the JSON contract

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

## Data used

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

## License

MIT, see [LICENSE](LICENSE).
