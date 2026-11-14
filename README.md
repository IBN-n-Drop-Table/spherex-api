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
curl "http://localhost:8000/api/v1/sky-patch?ra=08h55m10.831s&dec=-7d14m42.53s&band=H2O"
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

Full, always up-to-date schema: **`/docs`** (OpenAPI). A real response is in
[`docs/contract-example.json`](docs/contract-example.json). CORS is open (`*`).

| method | path | what |
|--------|------|------|
| `GET`  | `/api/v1/targets` | The 5 challenge objects (demo buttons) and whether they are cached |
| `GET`  | `/api/v1/sky-patch` | Timelapse (every visit), difference image, anomalies, spectrum, light curve |
| `POST` | `/api/v1/jobs` | Start (or reuse) a computation; returns a `job_id` at once |
| `GET`  | `/api/v1/jobs/{job_id}` | Poll: `status`, `stage`, `progress`, `message`, `eta_s`, `result` |
| `GET`  | `/api/v1/epochs` | Visits covering a position (metadata only, no downloads) |
| `GET`  | `/api/v1/spectrum` | 102-band spectrum of a target, an anomaly or a position |
| `GET/POST` | `/api/v1/reports` | Community feed (below) |

### Coordinates, field of view, band

- `ra` / `dec`: decimal degrees **or** sexagesimal: `ra=08h55m10.831s` (also `08:55:10.831`), `dec=-7d14m42.53s` (also `-07:14:42.53`). Bad input -> **422**.
- `fov`: **arcminutes**, 1-30, default 5 (same unit as the frontend URL `?fov=5`). Cached targets are always 5'; live jobs are capped at `LIVE_MAX_FOV_ARCMIN` (15).
- `band=H2O|CO2|CO`: only echoed back as `band`. Every response always carries all chemical filters (`chemical_filters`), and the frontend picks one.

### `GET /api/v1/sky-patch`

| situation | response |
|-----------|----------|
| `target` is cached, or (`ra`, `dec`) falls in a cached target's box | **200** `SkyPatchResponse`, instantly |
| a catalogue target without cached data, or any other position | **202** job (poll it) |
| unknown `target` and no coordinates | **404** |
| value out of range (`dec=200`, `fov=0.5`, `ra=25h`) | **422** |

What the frontend gets (all image URLs absolute):

- `timelapse_epochs[]`: **one entry per SPHEREx visit** (3 for Barnard's Star and WISE 0855), for the blink comparator. Same stretch in every epoch, so blinking is honest. `chemical_filters` has `H2O`, `CO2`, `CO` when that visit has exposures in the window.
- `difference_imaging`: `epoch_a` (last visit) minus `epoch_b` (first visit): the longest baseline, so the most motion (`kind: "first_vs_last"`). `detected_anomalies[]` come with `kind` (`appeared`/`disappeared`), `contamination_flags`, `is_clean_detection`, and the labels `detection_label` ("Detección Asistida por Software (BETA)") and `detection_notice` ("requiere verificación humana").
- `difference_imaging.pairwise[]`: one difference per pair of **consecutive** visits, for the blink comparator. See [Epoch differences](#epoch-differences-first-vs-last-and-consecutive).
- `spectrum_102_bands` (from `spectrum_epoch_id`) and `light_curve` (one point per visit). Where there is no valid photometry, `flux_mjy_sr` is `null` with a `note`, never 0.
- `saturated` / `spectrum_message`: bright stars (Barnard's Star, R Draconis) saturate SPHEREx. Their cores are **shown** in the PNGs but **excluded** from the spectrum and light curve.
- `fallback` / `fallback_message`: see below.

### Jobs with progress (coordinates not in the cache)

```bash
curl -X POST localhost:8000/api/v1/jobs -H 'content-type: application/json' \
     -d '{"ra": "00h42m44s", "dec": "+41d16m09s", "fov": 5}'
# 202 {"job_id": "...", "status": "queued", "stage": "queued", "progress": 0, "retry_after_ms": 1500, ...}
curl localhost:8000/api/v1/jobs/<job_id>
# {"status": "running", "stage": "download", "progress": 0.41,
#  "message": "Descargando recortes de IRSA: 7/18 (~25 s restantes)", "eta_s": 27, ...}
```

- Cached targets come back **200** with `status: completed` and the `result` at once.
- Poll every `retry_after_ms` (1500 ms). Stages: `queued` -> `query` -> `download` -> `stack` -> `render` -> `difference` (only with 3+ visits) -> `done` (or `failed`).
- A live result has the same `pairwise` differences as the Golden Path, built from the same stacks: no extra downloads.
- **A cold job takes tens of seconds to a few minutes**: each IRSA cutout takes ~12 s (it always carries a ~4 MB PSF table), and live mode downloads 1 cutout per detector and visit (~18). Measured on 2026-11-14: 40-100 s for Barnard's Star. The messages say so.
- The same query returns the same job. One job runs at a time; more than 8 queued -> **503** `Retry-After: 30`.
- Jobs live in memory (1 h after finishing). A restart forgets them, which is why the service runs 1 uvicorn worker.
- The legacy `GET /sky-patch` / `GET /spectrum` 202 + `Location` flow still works.

### Offline-first fallback

IRSA gets **5 s** (`SIA_BUDGET_S`, retries included) to answer. On a timeout, 429 or 5xx, or when the server has no science stack, the job still **completes**: its result is the cached patch nearest to the requested position, with

```json
"fallback": true,
"fallback_message": "Mostrando datos en caché debido a alta latencia en servidores de origen. ..."
```

`target_info.name` says which cached object is shown. A failure caused by IRSA never produces a 500. A position IRSA answers for but SPHEREx hasn't covered twice yet ends as `status: failed` with a readable `error`.

### `GET /api/v1/epochs?ra=..&dec=..`

Visit list (`epoch_id`, `date`, `date_start`, `date_end`, `n_images`, `n_per_detector`). It is instant for cached targets. Otherwise it makes one IRSA query (at most 5 s), with the same fallback. Use it to draw the timeline while a job runs.

### `GET /api/v1/spectrum`

Precomputed 102-band spectra, so clicking an anomaly needs no live processing.

- `?target=WISE_0855`: the target (photometry at the patch centre).
- `?target=WISE_0855&anomaly_id=anom_04`: one detected anomaly (404 if it doesn't exist).
- `?ra=..&dec=..[&radius_arcsec=6]`: the closest cached target centre or anomaly within the radius.

Anything else returns **202** with a live job. The response carries `saturated` / `spectrum_message` when relevant.

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
3. **JSON (only with `--rewrite-json`).** By default the bucket is only a **mirror**: `app/data/*.json` keep image paths relative to the API (`/static/golden_path/...`). The API serves the images itself and turns the paths into absolute URLs, so the demo keeps working even if Supabase is down. With `--rewrite-json`, the JSON files point at the bucket instead.

Keys: the script needs the **service_role** key, because it creates the bucket and uploads. Put it in `SUPABASE_SERVICE_ROLE_KEY`; if that is empty, the script uses `SUPABASE_KEY`. The API only needs the **anon** key in `SUPABASE_KEY`, so one `.env` works for both. Exit code 0 means ready, 1 means some step failed, 2 means credentials are missing.

To go back to local images after `--rewrite-json`, re-run `generate_golden_path.py`.

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
  routers/jobs.py       # POST /jobs, GET /jobs/{id}: jobs with progress
  routers/epochs.py     # /epochs (visit metadata) and /targets
  routers/common.py     # coordinates, public URLs, live jobs + fallback
  core/coords.py        # RA/Dec parser: degrees or sexagesimal (pure Python)
  core/urls.py          # relative image paths on disk <-> absolute URLs on the wire
  services/fallback.py  # offline-first: nearest cached patch when IRSA fails
  routers/reports.py    # /reports: submit, feed, verify
  services/golden_path.py  # lookup over cached targets and spectra
  services/jobs.py      # thread-safe in-memory job store (TTL, de-dup, limits)
  services/live.py      # runs the pipeline for uncached positions
  services/reports.py   # Supabase repo + in-memory fallback
  data/<target>.json    # generated Golden Path payloads
  static/golden_path/   # generated PNGs (--storage local)
scripts/generate_golden_path.py  # offline IRSA -> JSON/PNG pipeline
scripts/setup_supabase.py        # migration + bucket + optional PNG mirror
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

With `--storage local` (the default), PNGs go to `app/static/golden_path/` and are **committed to the repo**. The JSON stores relative paths (`/static/golden_path/...`), and the API prepends its public base (`PUBLIC_BASE`, or the request host) when it serves them.

To publish already-generated PNGs, use [`scripts/setup_supabase.py`](#supabase-setup-one-command). To upload straight from the pipeline instead, the bucket must already exist and be **public**:

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

1. **Finds the exposures.** It runs an IRSA SIA2 query on `spherex_dr1` and `spherex_qr3`, the current collections per the IRSA user guide. The query goes straight over HTTP with a time budget and retries (`--sia-budget-s`). It doesn't use `Irsa.query_sia`, whose `/SIA/capabilities` pre-flight was failing with 502 on 2026-11-14. If those collections have too few exposures, it also queries the deep fields. Duplicates across releases are removed.
2. **Uses every visit.** Exposures are grouped into survey visits by gaps in `t_min` (`--epoch-gap-days`, 30). It never uses fixed calendar semesters, which split a visit in two. Every visit becomes a timelapse epoch. The difference image compares the **first and the last** visit (`--epoch-strategy all`, the default).
3. **Downloads cutouts, not full files.** It uses IRSA's cutout service (`access_url?center=..&size=..`), which returns the same MEF layout: IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE. Measured on 2026-11-14: a 5′ cutout is ~4 MB and takes ~11-13 s. It is that heavy because IRSA always includes the full EPSF table, and no documented parameter removes it. A full frame is ~70 MB. This download is the pipeline's bottleneck, and its cost scales with the number of cutouts, not with the fov.
4. **Cleans each exposure.** It computes `IMAGE − ZODI` and masks artifact-flagged pixels (`STACK_MASK_FLAGS`). Saturated pixels (`OVERFLOW`/`BLOOM` within 3 px of an `OVERFLOW` core) are kept in a separate **display-only** copy: the PNGs show bright stars instead of a black hole, while photometry, spectra, light curves and detection never use them. Then it evaluates the spectral WCS at the target and measures aperture photometry. Finally it applies `Cutout2D` and `reproject_interp` onto a north-up, square-pixel TAN grid centred on the target's epoch-1 position. The grid covers exactly `--fov` (50 × 50 px of 6.0″ for 5′, close to SPHEREx's native 6.15″). The 600 px PNGs are an exact 12× upscale of it, which is what `patch.wcs` describes. It also records the LVF wavelength at every grid pixel, so anomalies get spectra at their own position.
5. **Corrects for proper motion.** The target's position is propagated to each exposure's date, using the astrometry in `app/core/targets.py`. Without this, Barnard's Star and WISE 0855 would land several arcminutes away from the centre of a 5′ field.
6. **Builds the products.**
   - RGB image: B = detectors 1–2, G = 3–4, R = 5–6.
   - H₂O (1.4–1.9 and 2.6–2.8 µm), CO₂ (4.2–4.4 µm) and CO (4.60–4.74 µm) filter images.
   - Difference image (last − first visit). Each detector's coadd is only ever subtracted from the same detector's coadd, because each detector sees different wavelengths.
   - Consecutive-visit differences (`pairwise`), from the same per-visit stacks: no re-stacking, no anomaly detection.
   - Anomalies found with OpenCV on the (first vs last) difference image's SNR map. Detections within `--dedup-radius-px` (12) PNG px are merged, and each remaining anomaly gets its own 102-band spectrum.
   - A 6 × 17 = 102-channel LVF spectrum, combined with inverse-variance weighting.
   - A light curve with one point per visit, averaged over the channels every visit shares. No valid photometry gives `null` plus a `note`, never 0.

   All epochs use the same display stretch, taken from the clean stacks, so a change in brightness shows up in the images and a saturated core clips to white.

### Things the frontend should know

**Patch geometry (`patch`).** Every image in a response (`image_url`, `chemical_filters`, `subtracted_image_url`) shares one geometry, given by `patch.width_px`, `patch.height_px` and `patch.wcs`:

- `patch.wcs` is a FITS WCS (CD-matrix form): north up, east left, square pixels, gnomonic (`RA---TAN`/`DEC--TAN`). It uses FITS pixel numbering: 1-based, +Y up, `crpix = (300.5, 300.5)` is the image centre. `cd1_1 = -(fov_arcmin/60)/width_px`, `cd2_2 = +(fov_arcmin/60)/height_px`.
- Aladin Lite v3 can take these keys directly, together with `NAXIS1 = width_px` and `NAXIS2 = height_px`.
- `crval1/crval2` is the **patch centre**: the target's position at the epoch-1 date. For fast-moving stars this is **not** `target_info.ra_deg/dec_deg`, which is the catalogue position. WISE 0855's 2010 position is about 2′ east of where it was in 2025. Centre the view on `crval`.

**Pixels.** `pixel_x` and `pixel_y` are **0-based PNG indices, origin top-left** (+Y down). To convert to FITS: `x_fits = pixel_x + 1`, `y_fits = height_px − pixel_y`. An anomaly marker can be drawn straight on the PNG, or placed on the sky with `ra_deg`/`dec_deg`; the two agree to within 0.5″.

**Formats.** `image_format` (`"png"` or `"fits"`) is set on each timelapse epoch (it also applies to that epoch's `chemical_filters`) and on `difference_imaging`. Everything cached is `png` today.

**Difference image.** `subtracted = epoch_a − epoch_b` = last visit − first visit (e.g. `2025_M12 − 2025_M0`). Red means brighter in `epoch_a`, blue brighter in `epoch_b`.

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
python scripts/setup_supabase.py      # optional: mirror the PNGs to Supabase
```

## Datos utilizados (Data used)

Everything the API serves comes from public archives. Nothing is simulated: where a measurement is impossible (e.g. saturated stars), the response says so (`null` + `note`, `saturated`, `spectrum_message`).

| Source | What we use it for | Access |
|--------|--------------------|--------|
| **SPHEREx** (NASA/JPL/Caltech), served by **IRSA/IPAC** | All images, difference images, spectra and light curves: Level 2 Spectral Images (MEF: IMAGE, FLAGS, VARIANCE, ZODI, WCS-WAVE), downloaded as server-side cutouts | IRSA SIA2 `https://irsa.ipac.caltech.edu/SIA`, collections **`spherex_dr1`** (DR1, DOI [10.26131/IRSA678](https://www.ipac.caltech.edu/doi/10.26131/IRSA678)) and **`spherex_qr3`** (QR3, DOI [10.26131/IRSA662](https://www.ipac.caltech.edu/doi/10.26131/IRSA662)); deep fields `spherex_dr1_deep` / `spherex_qr3_deep` only as a fallback. [SPHEREx at IRSA](https://irsa.ipac.caltech.edu/Missions/spherex.html), [Archive user guide](https://caltech-ipac.github.io/spherex-archive-documentation/), [tutorials](https://caltech-ipac.github.io/irsa-tutorials/spherex-intro/) |
| **Gaia DR3** (ESA) | Reference positions and proper motions of Barnard's Star, Epsilon Indi B, RW Aurigae and R Draconis (`app/core/targets.py`), propagated to each SPHEREx exposure date | [Gaia archive](https://gea.esac.esa.int/archive/); Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1 |
| **WISE** (NASA) | Reference position of WISE 0855−0714 (from its WISE designation, epoch ~2010.4); its proper motion is from Luhman & Esplin (2016, AJ 152, 78) | [WISE at IRSA](https://irsa.ipac.caltech.edu/Missions/wise.html) |

Gaia/WISE catalogue overlays in the viewer are drawn by the frontend (Aladin Lite), not by this API.

SPHEREx acknowledgement, as requested by IRSA ([source](https://irsa.ipac.caltech.edu/Missions/spherex.html)):

> This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration.

Canonical SPHEREx papers listed by IRSA: Bock et al. (2026, mission), Akeson et al. (2026, pipeline), Crill et al. (2025, sky simulator), Korngut et al. (2026, instrument & calibration).

Software: FastAPI, Astropy, Astroquery/PyVO, reproject, NumPy, OpenCV, Matplotlib, Pillow.

## License

MIT, see [LICENSE](LICENSE). (TODO: copyright holder name pending.)

## Adding a target

Add a `Target(...)` to `app/core/targets.py`, then run the pipeline for it. The API serves every catalogue target that has a JSON file, and checks each file against the schema at startup.
