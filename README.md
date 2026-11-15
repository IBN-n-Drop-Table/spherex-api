# spherex-api

### Watch the infrared sky change, with real SPHEREx data.

**Live at [eskai.earth](https://eskai.earth)** — *Eskai: look up.* Compare the infrared sky with what your eye sees, watch nearby stars move, and play find-what-moves.

`spherex-api` is the backend of Eskai, our NASA Space Apps 2026 citizen-science app. Type a position in the sky (or tap one of five ready-made objects) and get back a **blink-ready sequence of real SPHEREx images**, a **difference image** that highlights what changed between visits, and the **infrared spectrum** of whatever you find. In milliseconds for the featured objects, within about a minute for almost anywhere else.


---

## Why it's worth a look

- **Real data, nothing simulated.** Every image, spectrum and light curve is computed from public SPHEREx Level 2 data (NASA/JPL/Caltech) served by IRSA/IPAC. Gaia DR3 provides the positions and proper motions behind the "what should have moved" check.
- **See time pass.** One image per SPHEREx visit, all drawn with the *same* contrast, so a blink between visits is honest: if something changes, it really changed.
- **Motion you can verify.** Barnard's Star moves about 10″ a year. Our pipeline measured 10.3″ against 10.2″ expected from Gaia, and you can see it move between visits.
- **102 infrared colors.** SPHEREx splits infrared light into 102 channels. The API returns spectra with ready-made filters for **H₂O, CO₂ and CO**, so a frontend can show the chemistry, not just the picture. For Epsilon Indi B the spectrum shows the methane fingerprint.
- **Fast by design.** The 5 featured objects are precomputed: responses in a few milliseconds. Any other position runs as a background job with live progress, and never leaves you with a 500 error if the archive is slow (see [offline-first fallback](#offline-first-fallback)).
- **Honest by design.** When a measurement is impossible, the API says so with `null` plus a note, never a made-up zero. Saturated stars are drawn but excluded from the measurements, and every candidate is labelled as software-assisted and in need of human verification.
- **Built for citizen science.** The software proposes candidates, people verify them. A community feed lets users submit and confirm reports.
- **Ready to build on.** FastAPI with interactive docs at `/docs`, open CORS, typed responses, images that work directly in WebGL viewers such as Aladin Lite, and a documented geometry (`patch.wcs`) so overlays line up.

## The five featured objects

All five are served instantly from precomputed data, each with notes on what can and can't be claimed from it.

| Object | Why it's interesting | Visits | Valid spectrum channels |
|---|---|---|---|
| **Barnard's Star** | One of the fastest-moving stars in the sky, about 6 light years away | 3 | 0 (too bright, saturates the detector) |
| **WISE 0855−0714** | One of the coldest known brown dwarfs, about 7.4 light years away | 3 | 101 (blended with a neighbouring star at short wavelengths) |
| **Epsilon Indi B** | A nearby brown dwarf, about 12 light years away | 4 | 100 |
| **RW Aurigae** | A young, actively interacting star system, roughly 510 light years away | 3 | 58 (lower limits) |
| **R Draconis** | A bright variable red giant, roughly 2,600 light years away | 3 | 0 (saturated) |

Visits span April 2025 to September 2026. SPHEREx returns to the same patch of sky every 5 to 7 months, so you get three or four real snapshots over about a year. Distances come from Gaia parallaxes (WISE 0855 from Spitzer, since it isn't in Gaia). Per-object distance and its source are served by `GET /api/v1/targets`.

## Try it in 30 seconds

Want to see it first? Open **[eskai.earth](https://eskai.earth)**. To run the API yourself:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive API, then:

```bash
# A featured object, instantly
curl "http://localhost:8000/api/v1/sky-patch?target=BARNARDS_STAR"

# Any position, in degrees or sexagesimal
curl "http://localhost:8000/api/v1/sky-patch?ra=133.795&dec=-7.245&fov=5"
curl "http://localhost:8000/api/v1/sky-patch?ra=08h55m10.831s&dec=-7d14m42.53s&band=H2O"

# Somewhere new: starts a background job (202)
curl -i "http://localhost:8000/api/v1/sky-patch?ra=10.68&dec=41.27"
```

More calls to try (no AI keys or Supabase needed; the chat then answers with `provider: "canned"`):

```bash
B=http://localhost:8000
curl -s $B/api/v1/targets | python3 -m json.tool | head -30            # distances and subtitles
curl -s -X POST $B/api/v1/chat -H 'content-type: application/json' \
  -d '{"message":"What is a brown dwarf?","tone":"easy","context":{"target":"WISE_0855"}}'
curl -s -X POST $B/api/v1/game/sessions -H 'content-type: application/json' -d '{"rounds":3}'
curl -s "$B/api/v1/catalog-objects?target=BARNARDS_STAR&max_n=5&type_filter=no_common_stars"
```

> Target names are case-insensitive and tolerant of spelling (`WISE_0855`, `wise-0855` and `Barnard's Star` all work in the chat suggestions). Use `GET /api/v1/targets` for the exact keys.

## What you get back

A single `sky-patch` response carries everything a viewer needs:

| Field | What it is |
|---|---|
| `timelapse_epochs[]` | One image per SPHEREx visit, same contrast in each, for a blink comparator |
| `chemical_filters` | H₂O, CO₂ and CO filter images, per visit |
| `difference_imaging` | Last visit minus first visit (the longest baseline, so the most motion). Red means brighter later, blue brighter earlier |
| `detected_anomalies[]` | Candidates found in the difference image, each with its own spectrum, quality flags and the label *"software-assisted detection (BETA), requires human verification"* |
| `spectrum_102_bands` | Up to 102 channels, inverse-variance weighted |
| `light_curve` | One point per visit |
| `patch.wcs` | The exact sky geometry of every image, ready for Aladin Lite overlays |
| `fallback` / `fallback_message` | Tells you when saved data is being shown instead of live data |

The image is a **5′ patch** (about a sixth of the full Moon), rendered at 600 px. Fields of view from 1′ to 30′ are accepted. Featured objects are always 5′, live jobs go up to 15′.

## Honest by design

We would rather show a limit than hide it. Things the API does on purpose:

- **No fake zeros.** No valid photometry means `flux_mjy_sr: null` with an explanatory `note`.
- **Saturated stars are shown, not measured.** Barnard's Star and R Draconis burn the detector. Their cores appear in the images but are excluded from spectra and light curves, and the response includes `saturated` and `spectrum_message`.
- **Quality flags on every anomaly.** `contamination_flags` uses SPHEREx's own names (`SOURCE`, `HOT`, `GHOST`, `PERSIST`…).
- **Candidates, not discoveries.** Detection is software-assisted and needs a person to verify it. It is a good tool for spotting what to look at, not an automatic discovery machine. For example, WISE 0855 is only faintly visible at long infrared wavelengths and is not flagged automatically.
- **Visits, not video.** A timelapse here is 3 or 4 real visits over about a year, shown as a blink. It is not a continuous film and it is not real time.
- **Fails gracefully.** If the archive is slow or down, you get the nearest saved patch with a visible notice, not an error. If the AI providers are down, the chat answers with a clearly marked ready-made reply.
- **Caveats ship with the data.** `quality`, `science_note` and `weak_signal` travel in the response itself.

## Beyond the images

Everything below is already built in the backend. The frontend only needs to draw it.

| Capability | Endpoint | What it does |
|---|---|---|
| **Object stories** | `GET /api/v1/targets` | Distance in light years computed from parallax, with its citation, "the light you see left this star N years ago" subtitles and a plain-language description. Approximate distances (RW Aurigae, R Draconis) are worded as "roughly" |
| **Spot the mover game** | `POST /api/v1/game/sessions`, `/guess`, `/finish`, `GET /game/leaderboard` | Blink two real SPHEREx visits of Barnard's Star or Epsilon Indi B and click on what moved. 3 to 5 rounds from easy to hard, optional hint image, scoring and leaderboard computed on the server. The right answer comes from Gaia DR3 proper motion |
| **"Explain it easy" chat** | `POST /api/v1/chat`, `GET /api/v1/chat/suggestions` | Explains what's on screen in `easy` or `technical` tone, using the object's trusted data and saved SIMBAD data. Suggests 4 questions per object |
| **"What am I looking at?"** | `GET /api/v1/catalog-objects` | Objects catalogued in SIMBAD, already projected to pixels on the SPHEREx image, with positions moved to the visit date using proper motion |
| **Loading phrases** | `GET /api/v1/loading-phrases` | Light-hearted waiting-screen lines in five categories. Answers even when everything else is down |
| **Credits** | `GET /api/v1/credits` (also `/credits`) | Data sources, DOIs, acknowledgement and software, ready for a credits page |
| **Community feed** | `/api/v1/reports` | Submit, browse and peer-confirm citizen reports |

## Endpoints

Full, always up-to-date schema: **`/docs`** (OpenAPI). A real `sky-patch` response is in [`docs/contract-example.json`](docs/contract-example.json), the contract of the newer features is in [`docs/wow-contract.md`](docs/wow-contract.md) and the frontend hand-off in [`docs/frontend-handoff.md`](docs/frontend-handoff.md). Base path: `/api/v1`. CORS is open (`*`), and the `Location` and `Retry-After` headers are exposed to the browser.

**All user-facing text is in English**, including fields whose name ends in `_es` (`description_es`, `story_es`, `explanation_es`). The names are kept so the contract doesn't break.

**One error format everywhere:**

```json
{"detail": "Game not found.", "code": "not_found", "errors": null}
```

`detail` is always display-ready text. `code` is stable, so branch on it: `not_found`, `validation_error`, `job_not_found`, `http_<status>` (for example `http_409`, `http_429`), `service_unavailable`, `internal_error`. `errors` is only filled on validation 422s, with `{loc, msg, type}` per field.

| method | path | what |
|---|---|---|
| `GET` | `/api/v1/targets` | The 5 challenge objects (demo buttons) and whether they are cached |
| `GET` | `/api/v1/sky-patch` | Timelapse (every visit), difference image, anomalies, spectrum, light curve |
| `POST` | `/api/v1/jobs` | Start (or reuse) a computation; returns a `job_id` at once |
| `GET` | `/api/v1/jobs/{job_id}` | Poll: `status`, `stage`, `progress`, `message`, `eta_s`, `result` |
| `GET` | `/api/v1/epochs` | Visits covering a position (metadata only, no downloads) |
| `GET` | `/api/v1/spectrum` | 102-band spectrum of a target, an anomaly or a position |
| `GET` | `/api/v1/catalog-objects` | Catalogued objects in view, in image pixels (SIMBAD, with fallbacks) |
| `POST` | `/api/v1/chat` | "Explain it easy" assistant |
| `GET` | `/api/v1/chat/suggestions` | 4 suggested questions, per object |
| `POST` | `/api/v1/game/sessions` | Start a game (3 to 5 rounds) |
| `POST` | `/api/v1/game/guess` | One attempt per round |
| `POST` | `/api/v1/game/finish` | Save the score (computed by the server) |
| `GET` | `/api/v1/game/leaderboard` | Top 20 |
| `GET` | `/api/v1/loading-phrases` | Waiting-screen phrases |
| `GET` | `/api/v1/credits` | Data credits and acknowledgement (also at `/credits`) |
| `GET/POST` | `/api/v1/reports` | Community feed ([below](#citizen-reports-community-feed)) |

### Coordinates, field of view, band

- `ra` / `dec`: decimal degrees **or** sexagesimal: `ra=08h55m10.831s` (also `08:55:10.831`), `dec=-7d14m42.53s` (also `-07:14:42.53`). Bad input gives **422**.
- `fov`: **arcminutes**, 1-30, default 5 (same unit as the frontend URL `?fov=5`). Cached targets are always 5′. Live jobs are capped at `LIVE_MAX_FOV_ARCMIN` (15).
- `band=H2O|CO2|CO`: only echoed back as `band`. Every response always carries all chemical filters (`chemical_filters`), and the frontend picks one.

### `GET /api/v1/sky-patch`

| situation | response |
|---|---|
| `target` is cached, or (`ra`, `dec`) falls in a cached target's box | **200** `SkyPatchResponse`, instantly |
| a catalogue target without cached data, or any other position | **202** job (poll it) |
| unknown `target` and no coordinates | **404** |
| value out of range (`dec=200`, `fov=0.5`, `ra=25h`) | **422** |

All image URLs are absolute. Details of the main fields:

- `timelapse_epochs[]`: **one entry per SPHEREx visit**, for the blink comparator. Same stretch in every epoch, so blinking is honest. `chemical_filters` has `H2O`, `CO2`, `CO` when that visit has exposures in the window.
- `difference_imaging`: `epoch_a` (last visit) minus `epoch_b` (first visit). `detected_anomalies[]` come with `kind` (`appeared`/`disappeared`), `contamination_flags`, `is_clean_detection`, and the labels `detection_label` ("Software-Assisted Detection (BETA)") and `detection_notice` ("Anomaly detected by software: requires human verification"). Always show them with any anomaly marker.
- `spectrum_102_bands` (from `spectrum_epoch_id`) and `light_curve` (one point per visit). Where there is no valid photometry, `flux_mjy_sr` is `null` with a `note`, never 0.
- `saturated` / `spectrum_message`: bright stars (Barnard's Star, R Draconis) saturate SPHEREx. Their cores are **shown** in the PNGs but **excluded** from the spectrum and light curve.
- `fallback` / `fallback_message` / `fallback_reason`: see [below](#offline-first-fallback).
- `science_note`, `quality` and `data_credits`: the scientific caveats and citations for this patch (details [below](#caveats-that-travel-with-the-data)).
- `difference_imaging` also carries `kind`, `delta_t_days`, `detectors_used`, `expected_shift_arcsec`, `weak_signal` and a `pairwise` list with one subtraction per pair of consecutive visits.

### Jobs with progress (positions not in the cache)

```bash
curl -X POST localhost:8000/api/v1/jobs -H 'content-type: application/json' \
     -d '{"ra": "00h42m44s", "dec": "+41d16m09s", "fov": 5}'
# 202 {"job_id": "...", "status": "queued", "stage": "queued", "progress": 0, "retry_after_ms": 1500, ...}
curl localhost:8000/api/v1/jobs/<job_id>
# {"status": "running", "stage": "download", "progress": 0.41,
#  "message": "<progress text in English, e.g. cutouts downloaded so far and time left>", "eta_s": 27, ...}
```

- Cached targets come back **200** with `status: completed` and the `result` at once.
- Poll every `retry_after_ms` (1500 ms). Stages: `queued` → `query` → `download` → `stack` → `render` → `done` (or `failed`).
- **A cold job takes tens of seconds to a few minutes**: each IRSA cutout takes ~12 s (it always carries a ~4 MB PSF table), and live mode downloads 1 cutout per detector and visit (~18). Measured on 2026-11-14: 40-100 s for Barnard's Star. The progress messages say so.
- `POST /jobs` takes `target` **or** `ra` + `dec`, plus `fov`, `band` and a `kind` that chooses a sky-patch or a spectrum job. Neither a target nor coordinates gives 422.
- A job lost to a server restart (or expired) answers **404** with `code: "job_not_found"` and the text *"The analysis was lost because the server restarted. Please start it again."* Re-launch `POST /jobs` with the same coordinates instead of showing an error.
- The same query returns the same job while it is active or completed. A failed job is not reused: asking again retries. By default one job runs at a time (IRSA throttles parallel downloads) and the rest wait as `queued`. Past the cap on active jobs (8 by default), new work gets **503** with `Retry-After: 30`.
- `eta_s` appears once a job is past about 6% progress. A failed job's `error` is one readable line (`ErrorType: message`), with no HTML error pages or stack traces.
- Jobs live in memory (1 h after finishing). A restart forgets them, which is why the service runs 1 uvicorn worker. When a job expires, its temporary images (`app/static/live/<job_id>/`) are deleted too.
- The legacy `GET /sky-patch` / `GET /spectrum` 202 + `Location` flow still works.

Live jobs run the pipeline inside the API process: the same code as the offline generator, so the output has the same contract. To enable them, run `pip install -r requirements-science.txt` in the same venv. `/health` shows `"live_processing": "available"` when it's ready.

How live mode keeps itself cheap: it downloads only a few cutouts per detector and visit (`LIVE_MAX_PER_DETECTOR`), caps the field of view (`LIVE_MAX_FOV_ARCMIN`), does not use astropy's download cache (free hosting has ephemeral disk) and imports the science stack lazily. At startup it warms that stack up in a background thread so the first job doesn't pay several seconds of imports; set `LIVE_WARMUP=0` to skip it and save about 100 MB of RAM until the first job. Measured on 2026-11-14: peak memory around 200 MB for Barnard's Star. A position outside the 5 objects is treated as a fixed point in the sky, with no proper-motion correction. If the science stack is missing, the job falls back to cached data, and says so.

### Offline-first fallback

IRSA gets **5 s** (`SIA_BUDGET_S`, retries included) to answer. On a timeout, 429 or 5xx, or when the server has no science stack, the job still **completes**: its result is the cached patch nearest to the requested position, with

```json
"fallback": true,
"fallback_reason": "irsa_unavailable",
"fallback_message": "Showing cached data because the source servers are responding slowly. ..."
```

`fallback_reason` is `null` when there was no fallback, otherwise `irsa_unavailable`, `live_processing_unavailable` or `unknown`, and the message is worded for that cause. If the nearest cached object is more than 0.25° away, the message adds which object is shown and how far it is. For `live_processing_unavailable` it tells the user that live analysis isn't available on this server right now and suggests picking one of the 5 objects. `/spectrum` falls back the same way. `target_info.name` says which cached object is shown. A failure caused by IRSA never produces a 500. A position IRSA answers for but SPHEREx hasn't covered twice yet ends as `status: failed` with a readable `error`.

### `GET /api/v1/epochs?ra=..&dec=..`

Visit list (`epoch_id`, `date`, `date_start`, `date_end`, `n_images`, `n_per_detector`, plus `fallback_reason`). Instant for cached targets. Otherwise it makes one IRSA query (at most 5 s), with the same fallback. Use it to draw the timeline while a job runs.

### `GET /api/v1/spectrum`

Precomputed 102-band spectra, so clicking an anomaly needs no live processing.

- `?target=WISE_0855`: the target (photometry at the patch centre).
- `?target=WISE_0855&anomaly_id=anom_04`: one detected anomaly (404 if it doesn't exist).
- `?ra=..&dec=..[&radius_arcsec=6]`: the closest cached target centre or anomaly within the radius (default 6″, up to 60″; a SPHEREx pixel is 6.15″).

Anything else returns **202** with a live job. The response carries `saturated` / `spectrum_message` when relevant.

### Caveats that travel with the data

The API attaches the scientific caveats to the response, so a frontend can't show a number without its context.

| Field | Values | Meaning |
|---|---|---|
| `quality` | `null`, `"low"`, `"unvalidated"` | `low`: saturated core, numbers are lower limits (Barnard's Star, R Draconis). `unvalidated`: WISE 0855, blended with a neighbouring fixed star about 11″ away |
| `science_note` | text or `null` | What can and can't be said about this patch. Show it whenever it isn't `null` |
| `weak_signal` | bool | `true` when the expected shift is smaller than one SPHEREx pixel (6.15″). Always `true` for consecutive pairs of the 5 objects |
| `data_credits` | list | Citations for the data behind this response |

These are added **at serve time**, on the way out, to every sky patch and spectrum (cached, live, fallback and job results). The saved Golden Path files are never modified. The rules: each object has its own note and quality; any `saturated: true` response gets `quality: "low"` on the response and on every spectral band, plus a lower-limit note on light-curve points that have a flux; and every patch and spectrum carries `data_credits`. Stored texts that were written in Spanish are converted to English at the same step.

Real example, Barnard's Star: *"Its proper motion does show: between the first and the last visit its centroid moves 10.3″ north, against 10.2″ expected."* For WISE 0855, motion is confirmed only in detector 6 (4.4 to 5 µm): 7.9″ west against 8.05″ expected, and the patch follows its proper motion rather than claiming a detection.

### `POST /api/v1/chat`

```json
{"message": "Why does it move?", "tone": "easy",
 "context": {"target": "BARNARDS_STAR", "blink": true, "weak_signal": true, "saturated": true}}
```

| Input | Limit |
|---|---|
| `message` | required, 1 to 500 characters |
| `tone` | `"easy"` (default) or `"technical"` |
| `context` | free object, up to 4 KB of JSON. `science_note`, `quality: "low"`, `saturated: true`, `weak_signal: true` and `fallback_reason` are detected at any depth |
| `history` | up to 20 turns, the last 6 are used |

If `context.target` is one of the 5 objects, the server adds its trusted data (distance, description) and **saved** SIMBAD data (the chat never queries SIMBAD live) to the prompt.

Response fields: `answer` (English), `provider` (`groq`, `deepseek` or `canned`), `cached`, `fallback`, `disclaimer` (*"AI assistant: it can make mistakes. Any finding requires human verification."*), `cautions[]` (derived from the context, show them next to the answer) and an optional `sources: ["SIMBAD"]`.

- **Never a 5xx because of the AI providers.** With no keys, both providers down or the daily cap reached, it answers with a ready-made reply and `fallback: true`. Possible errors are 422 and 429.
- Providers: Groq first, DeepSeek as backup, both through an OpenAI-compatible API. Keys live only on the server. Each provider gets 8 s in total, with at most one retry, and only for fast transient failures (connection error, 429, 5xx). A timeout isn't retried. Answers are capped at 600 tokens. Errors are logged without the key, the request or the response body. Limits: 8 requests per minute per IP, 1,000 provider calls per UTC day overall. Identical questions (same context, tone and history) are answered from a one-hour cache.
- Built to resist misuse: the system prompt is fixed on the server, the client's `context` and `history` are treated as untrusted data and can't change its rules, `cautions` are derived from the context on the server (not taken from the model), and trusted facts about an object come from the server, never from the client. Answers are kept short (120 words at most) and the model is told to say "I don't know" and never to call anything a discovery.
- Ready-made replies are picked by keywords. A question that doesn't match gets a generic text with `fallback: true`.
- The chat remembers nothing between requests. Resend `history`.

`GET /api/v1/chat/suggestions?target=WISE_0855` returns 4 questions for that object (key or name accepted). Without a known `target` it returns a generic list and `"target": null`.

### Game: "Find what moves"

Uses only **Barnard's Star and Epsilon Indi B**. The server keeps the right answer and reveals it only when the round is answered. Rounds never use the anomaly detector.

1. `POST /game/sessions` with an optional `{"rounds": 3}` (3 to 5, default 5). Rounds come ordered from easy to hard (with 5 rounds: 1 `easy`, 3 `medium`, 1 `hard`). Each round has two 300 × 300 crops of the same spot at different visits, `image_b_url` (earlier) and `image_a_url` (later), plus `delta_t_days` and `expected_shift_arcsec`.
2. The browser alternates the two images (the blink effect). Rounds with `weak_signal: true` also offer a `hint_image_url`: the later-minus-earlier subtraction, where a moving object leaves a red-blue pair.
3. The player clicks. Convert the click to **image pixels** (`x = clickX * image_size.width / displayedWidth`, origin top-left, +y down) and call `POST /game/guess` with `session_id`, `round_id`, `x`, `y`, `time_ms`. **One attempt per round.**
4. The response has `hit`, `distance_px`, `score`, `answer`, `answer_path` (position at both visits, to draw an arrow), `hit_radius_px` and an `explanation_es` with the real measurement (for example Barnard's Star moved about 10.2″ in 359 days).
5. `POST /game/finish` with `session_id` and `nickname` saves the score. Rounds not played count 0, and at least one must be played.
6. `GET /game/leaderboard` returns `{"entries": [...], "backend": "memory" | "supabase"}`, top 20, ties won by the oldest score. If Supabase fails mid-demo, the leaderboard drops to memory and stays there until restart, so it never flips between two stores. In memory it keeps the best 1,000 scores.

**Two kinds of rounds.** When the round pool exists (`app/data/game_pool/manifest.json` and `app/static/game_pool/*.png`, built offline by [`scripts/build_game_pool.py`](scripts/build_game_pool.py)), every session draws distinct rounds from it: one nearby high proper-motion star per round, **WISE (2010) vs SPHEREx DR1 (2025-26)**, `source: "wise_vs_spherex"`. The SPHEREx image comes from the DR1 HiPS cube on the public S3 bucket `nasa-irsa-spherex` (12.9″ pixels, several visits combined: see `date_a_range`), so building the pool needs no IRSA. The answer is still Gaia DR3 propagated to each image date. If the pool is missing, unreadable or smaller than the session, the session uses the Golden Path rounds below and says `"fallback": true`; `GAME_POOL=0` turns the pool off (`fallback` is then `null`). Status, validation and limitations: [docs/game-pool.md](docs/game-pool.md). New optional round fields: `source`, `telescope_a`/`telescope_b`, `date_a_range`/`date_b_range`, `region` (`ra_deg`, `dec_deg`, `fov_arcmin`, for the viewer link), `pixel_scale_arcsec`, `spherex_pixel_scale_arcsec` and `hit_radius_px` (15″ in pool rounds, 20 px in Golden Path rounds).

**How Golden Path rounds are built.** The right answer is Gaia DR3 position plus proper motion, propagated to each visit date and projected with the patch WCS. Checked against the cached images, the star's light centroid is within 0.7 px (0.35″) of that prediction in all 7 visits, and `tests/test_game.py` repeats the check. Every patch is centred on the object at its first visit, so each round shows a 300 px window cut from the 600 px images, placed so the object lands on one of 25 positions (a 5 × 5 grid). The same window is cut from both visits and from the difference image used for the hint. Crop file names are salted hashes, so they don't reveal the window. A hit radius of 20 px is about 10″, roughly the size of the star's blob. The distance counted is to the segment joining the object's two positions.

**Scoring** (0 to 1,000 per round, always computed by the server): a miss (more than 20 px from the object's path) scores 0. A hit scores `500 + 300·(1 − d/20) + 200·max(0, 1 − t/30 s)`, with `d` in pixels and `t` = `time_ms`. Sending `score` or any extra field returns 422. Nicknames are 3 to 16 characters: letters (accents and ñ allowed), digits, `_` and `-`.

| Code | When |
|---|---|
| 404 | Unknown session or round (also after a server restart) |
| 409 | Second attempt in the same round, game already finished, or `finish` with no rounds played |
| 410 | Session expired (15 minutes without activity) |
| 422 | `x`/`y` outside the image, `time_ms` outside 0 to 600,000, bad nickname or forbidden field |
| 429 | Too many requests, read `Retry-After` |
| 503 | Too many games in progress, or the cached data is missing |

### `GET /api/v1/catalog-objects`

Objects that SIMBAD has catalogued inside the SPHEREx image, already in pixels so a frontend can draw markers. **These are catalogue entries, not SPHEREx detections**, and the response says so in `notice`.

- Parameters: `target` **or** `ra`+`dec` (as `/sky-patch`), `epoch` (Golden Path only), `max_n` (1 to 50, default 20), `type_filter` (`all`, `no_common_stars`, `ir_galaxies`). Order is by number of bibliographic references, most cited first.
- `x`, `y` are pixels of the image the user sees, origin top-left, as in the game. No axis needs flipping: `left = x * displayedWidth / image_size.width`.
- Positions are moved from J2000 to the visit date using SIMBAD proper motion. Objects without proper motion carry `proper_motion_applied: false`. Barnard's Star shifts about 6′ since J2000, so the search cone widens to catch fast movers.
- Answers come from a one-hour in-memory cache when possible. The cache key rounds the centre and radius to 0.001° and the date up to the next quarter of a year, so close requests share an entry. Magnitude is the first available of V, G, R, J, H, K. Ties in the ordering are broken by identifier, so it is stable.
- Always 200 for a valid request. If SIMBAD is down (or this server is pacing its own queries) you get, in order, the last cached result for the same query even if stale, the precomputed file for the object, or an empty list, with `source` (`simbad` | `cache` | `precomputed` | `none`), `fallback_reason` (`simbad_unavailable` | `simbad_rate_limited`) and an English `fallback_message`.
- The four catalogued planets of Barnard's Star sit at the star's own position, so markers overlap. Group them.
- Limit: 20 requests per IP per minute. The first request for a new field can take up to about 5 s.

### Loading phrases and credits

`GET /api/v1/loading-phrases` returns light-hearted English lines in five categories: `generic`, `cold_start`, `subtraction`, `game` and `chat`. It doesn't depend on the database, the AI or the cache, and carries `Cache-Control: public, max-age=3600`. `GET /api/v1/credits` returns `data_credits`, the IRSA `acknowledgement` and the `software` list.

### Citizen reports (community feed)

| method | path | notes |
|---|---|---|
| `POST` | `/api/v1/reports` | Submit a report. Returns 201 with the stored report. |
| `GET` | `/api/v1/reports` | Feed, newest first. Optional `target_name`, `classification`, `limit` (1-100, default 20). |
| `POST` | `/api/v1/reports/{id}/verify` | Peer confirmation: `verifications_count + 1`. Returns 404 for an unknown id. |

```bash
curl -X POST localhost:8000/api/v1/reports -H 'content-type: application/json' -d '{
  "discoverer": "stargazer_ana", "ra_deg": 133.758282, "dec_deg": -7.241336,
  "target_name": "WISE_0855", "pixel_x": 312, "pixel_y": 293,
  "classification": "brown_dwarf", "anomaly_id": "anom_09"}'
curl "localhost:8000/api/v1/reports?target_name=WISE_0855&limit=10"
curl -X POST localhost:8000/api/v1/reports/<id>/verify
```

`classification` is one of `asteroid`, `brown_dwarf`, `high_proper_motion_star`, `variable_star`, `transient_anomaly`, `other`. `pixel_x`/`pixel_y` must be sent together. Catalogue target names are stored in a single spelling (`wise_0855` becomes `WISE 0855`), so the feed filter accepts the same spellings as `/sky-patch`.

#### Storage: Supabase, or in-memory fallback

Run [`scripts/setup_supabase.py`](#supabase-setup-one-command), or apply the migration by hand (SQL Editor). Then start the API with `--env-file .env`.

`GET /health` shows the store in use (`"reports_backend": "supabase"` or `"memory"`). If the variables are unset, the table is missing or Supabase can't be reached, the API logs a warning and uses an in-memory store. That store starts **empty**: set `REPORTS_DEMO_SEED=1` to load two demo examples (WISE 0855 and Barnard's Star), which are flagged `is_example: true`, use the handle `ejemplo_demo` and start their notes with `[EJEMPLO]`, so they are never mistaken for real reports. If Supabase fails mid-demo, the API switches to memory and stays there until restart, so the feed doesn't jump between two stores. Reports kept in memory are lost on restart.

The game leaderboard has its own table. To make it survive restarts, paste [`supabase/game_scores.sql`](supabase/game_scores.sql) into **Dashboard → SQL Editor → Run** (it is idempotent) and **restart the API**, which checks the table once at startup. `GET /game/leaderboard` shows `"backend": "supabase"` or `"memory"`. Keep the `anon` key on the server only: it can insert rows directly, and the SQL file has an optional commented block to restrict inserts to `service_role`.

Database rules: anyone can read and submit reports. Clients cannot set `id`, `created_at` or `verifications_count`, and nobody can update or delete rows through the API. `verify_citizen_report()` increments the counter atomically. Verification is anonymous, so one person can confirm a report more than once. Fixing that needs user accounts (Supabase Auth plus a `report_verifications` table).

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

### Smoke test a deployment

[`scripts/smoke_test.py`](scripts/smoke_test.py) checks a running API from the outside, using only the standard library:

```bash
python scripts/smoke_test.py https://<your-api-host>
python scripts/smoke_test.py http://127.0.0.1:8000 --skip-live
```

It checks `/health`, the 5 featured objects on `/sky-patch` and `/spectrum`, `/credits` (both SPHEREx DOIs and the acknowledgement), that every image URL uses the scheme and host of the base URL (https on a public deploy), CORS on a PNG and on a preflight, the error format, a lost job, and a real `POST /jobs` polled until it finishes (a 2′ field by default, override with `--ra`, `--dec`, `--fov`). A sleeping free-tier server is given up to `--wake-timeout` seconds (default 120) to wake up. It prints one line per check (`OK` or `FALLO`, Spanish for "failed"), and exits with 1 if anything failed.

### Refresh the contract example

```bash
python scripts/export_contract_example.py [--target WISE_0855] [--base http://localhost:8000]
```

Writes `docs/contract-example.json` from a **real** `/sky-patch` response (through the actual API, so it is exactly what the frontend receives), plus real examples of a lost job and `/credits`. Anomaly spectra are trimmed to 3 channels. Re-run it whenever the response text or fields change.

## Supabase setup (one command)

[`scripts/setup_supabase.py`](scripts/setup_supabase.py) prepares Supabase and publishes the PNGs already generated in `app/static/golden_path/`. It does **not** re-run the FITS pipeline, and it is safe to run again.

```bash
cp .env.example .env        # fill in SUPABASE_URL, the keys and SUPABASE_BUCKET
.venv/bin/python scripts/setup_supabase.py --dry-run   # validate + show the plan, no network
.venv/bin/python scripts/setup_supabase.py
.venv/bin/uvicorn app.main:app --reload --port 8000 --env-file .env
```

1. **Database.** If `SUPABASE_DB_URL` is set and `psycopg` is installed (`pip install "psycopg[binary]"`), it applies [`supabase/migrations/20261114000000_citizen_reports.sql`](supabase/migrations/20261114000000_citizen_reports.sql). If not, it checks that `citizen_reports` is reachable and, if the table isn't there, prints how to paste the SQL into **Dashboard → SQL Editor** once.
2. **Storage.** It creates the public bucket `SUPABASE_BUCKET` (default `spherex-golden-path`), or makes an existing one public, then uploads every PNG to `golden_path/<file>`, overwriting existing files.
3. **JSON (only with `--rewrite-json`).** By default the bucket is only a **mirror**: `app/data/*.json` keep image paths relative to the API (`/static/golden_path/...`). The API serves the images itself and turns the paths into absolute URLs, so the demo keeps working even if Supabase is down. With `--rewrite-json`, the JSON files point at the bucket instead.

The script needs the **service_role** key (`SUPABASE_SERVICE_ROLE_KEY`, or `SUPABASE_KEY` if that is empty) because it creates the bucket and uploads. The API itself only needs the **anon** key in `SUPABASE_KEY`, so one `.env` works for both. Exit code 0 means ready, 1 means some step failed, 2 means credentials are missing. To go back to local images after `--rewrite-json`, re-run `generate_golden_path.py`.

## Under the hood: the Golden Path pipeline

`scripts/generate_golden_path.py` turns real SPHEREx Level 2 data on IRSA into the JSON and PNGs the API serves. Run it before a demo, not during it.

```bash
pip install -r requirements-science.txt

# Quick smoke test (~2 min): thin each epoch to 24 exposures
python scripts/generate_golden_path.py --target WISE_0855 --max-exposures-per-epoch 24

# Full run for one target (~10 min, ~230 cutouts)
python scripts/generate_golden_path.py --target WISE_0855

# All 5 targets
python scripts/generate_golden_path.py --all
```

With `--storage local` (the default), PNGs go to `app/static/golden_path/` and are **committed to the repo**. The JSON stores relative paths (`/static/golden_path/...`), and the API prepends its public base (`PUBLIC_BASE`, or the request host) when it serves them. To upload straight from the pipeline instead, the bucket must already exist and be **public**:

```bash
export SUPABASE_URL="https://<project>.supabase.co"
export SUPABASE_KEY="<service-role key>"   # needs write access to Storage
export SUPABASE_BUCKET="spherex-golden-path"
python scripts/generate_golden_path.py --all --storage supabase
```

Add `--offline` (or `CACHE_ONLY=1`) to reuse only what is already in the cache, `--skip-missing` to carry on when a target has no data, and `--output-root` to write somewhere else.

Downloaded cutouts are stored in astropy's download cache (`~/.astropy/cache`), so a re-run only reprocesses them. Pass `--no-cache` to download again, or `--help` for all options.

### What it does

1. **Finds the exposures.** An IRSA SIA2 query on `spherex_dr1` and `spherex_qr3`, the current collections per the IRSA user guide. It goes straight over HTTP with a time budget and retries (`--sia-budget-s`), and does not use `Irsa.query_sia`, whose `/SIA/capabilities` pre-flight was failing with 502 on 2026-11-14. If those collections have too few exposures, it also queries the deep fields. Duplicates across releases are removed.
2. **Uses every visit.** Exposures are grouped into survey visits by gaps in `t_min` (`--epoch-gap-days`, 30), never by fixed calendar semesters, which split a visit in two. Every visit becomes a timelapse epoch. The difference image compares the **first and the last** visit (`--epoch-strategy all`, the default).
3. **Downloads cutouts, not full files.** It uses IRSA's cutout service (`access_url?center=..&size=..`), which returns the same MEF layout: IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE. Measured on 2026-11-14: a 5′ cutout is ~4 MB and takes ~11-13 s, because IRSA always includes the full EPSF table and no documented parameter removes it. A full frame is ~70 MB. This download is the pipeline's bottleneck, and its cost scales with the number of cutouts, not with the fov.
4. **Cleans each exposure.** It computes `IMAGE − ZODI` and masks artifact-flagged pixels (`STACK_MASK_FLAGS`). Saturated pixels (`OVERFLOW`/`BLOOM` within 3 px of an `OVERFLOW` core) are kept in a separate **display-only** copy: the PNGs show bright stars instead of a black hole, while photometry, spectra, light curves and detection never use them. It then evaluates the spectral WCS at the target, measures aperture photometry, and applies `Cutout2D` and `reproject_interp` onto a north-up, square-pixel TAN grid centred on the target's epoch-1 position. The grid covers exactly `--fov` (50 × 50 px of 6.0″ for 5′, close to SPHEREx's native 6.15″). The 600 px PNGs are an exact 12× upscale of it, which is what `patch.wcs` describes. It also records the LVF wavelength at every grid pixel, so anomalies get spectra at their own position.
5. **Corrects for proper motion.** The target's position is propagated to each exposure's date, using the astrometry in `app/core/targets.py`. Without this, Barnard's Star and WISE 0855 would land several arcminutes away from the centre of a 5′ field.
6. **Builds the products.**
   - RGB image: B = detectors 1-2, G = 3-4, R = 5-6.
   - H₂O (1.4-1.9 and 2.6-2.8 µm), CO₂ (4.2-4.4 µm) and CO (4.60-4.74 µm) filter images.
   - Difference image (last − first visit). Each detector's coadd is only ever subtracted from the same detector's coadd, because each detector sees different wavelengths.
   - Anomalies found with OpenCV on the difference image's SNR map. Detections within `--dedup-radius-px` (12) PNG px are merged, and each remaining anomaly gets its own 102-band spectrum.
   - A 6 × 17 = 102-channel LVF spectrum, combined with inverse-variance weighting.
   - A light curve with one point per visit, averaged over the channels every visit shares. No valid photometry gives `null` plus a `note`, never 0.

   All epochs share the same display stretch, taken from the clean stacks, so a change in brightness shows up in the images and a saturated core clips to white.

**SIMBAD lists.** [`scripts/generate_simbad_golden.py`](scripts/generate_simbad_golden.py) precomputes `app/data/simbad/<target>.json` for the 5 objects. The API serves these when SIMBAD doesn't answer (`source: "precomputed"`), and the chat reads them too. Each file keeps the unfiltered J2000 rows of the cone search, so any `type_filter`, `max_n` and `epoch` can be served from it. It needs only the network, nothing from `requirements-science.txt`.

```bash
python scripts/generate_simbad_golden.py                  # all 5 targets
python scripts/generate_simbad_golden.py --target BARNARDS_STAR
python scripts/generate_simbad_golden.py --dry-run        # print the ADQL, no network
python scripts/generate_simbad_golden.py --check-otypes   # are our type codes known to SIMBAD?
python scripts/generate_simbad_golden.py --tap-url https://simbad.cfa.harvard.edu/simbad/sim-tap/sync
```

A target is written only if its query succeeds, so a failed run never leaves a half-written file. As a sanity check it reports where the target itself lands on the epoch-1 image: it must be within 5 px of the centre.

The API validates every `app/data/*.json` file at startup and refuses to start if one is out of date. After changing the JSON contract, regenerate the files (quick when the cutouts are in the astropy cache), then republish if you use Supabase:

```bash
python scripts/generate_golden_path.py --all --workers 2
python scripts/setup_supabase.py      # optional: mirror the PNGs to Supabase
```

**Keeping it fresh.** Data comes from the SPHEREx DR1 and QR3 releases. Updating for new releases means re-running the pipeline script above (a manual step today).

## For frontend developers

**Patch geometry (`patch`).** Every image in a response (`image_url`, `chemical_filters`, `subtracted_image_url`) shares one geometry, given by `patch.width_px`, `patch.height_px` and `patch.wcs`:

- `patch.wcs` is a FITS WCS (CD-matrix form): north up, east left, square pixels, gnomonic (`RA---TAN`/`DEC--TAN`). It uses FITS pixel numbering: 1-based, +Y up, `crpix = (300.5, 300.5)` is the image centre. `cd1_1 = -(fov_arcmin/60)/width_px`, `cd2_2 = +(fov_arcmin/60)/height_px`.
- Aladin Lite v3 can take these keys directly, together with `NAXIS1 = width_px` and `NAXIS2 = height_px`.
- `crval1/crval2` is the **patch centre**: the target's position at the epoch-1 date. For fast-moving stars this is **not** `target_info.ra_deg/dec_deg`, which is the catalogue position. WISE 0855's 2010 position is about 2′ east of where it was in 2025. Centre the view on `crval`.

**Pixels.** `pixel_x` and `pixel_y` are **0-based PNG indices, origin top-left** (+Y down). To convert to FITS: `x_fits = pixel_x + 1`, `y_fits = height_px − pixel_y`. An anomaly marker can be drawn straight on the PNG, or placed on the sky with `ra_deg`/`dec_deg`; the two agree to within 0.5″.

**Formats.** `image_format` (`"png"` or `"fits"`) is set on each timelapse epoch (it also applies to that epoch's `chemical_filters`) and on `difference_imaging`. Everything cached is `png` today.

**Difference image.** `subtracted = epoch_a − epoch_b` = last visit − first visit (e.g. `2025_M12 − 2025_M0`). Red means brighter in `epoch_a`, blue brighter in `epoch_b`.

**Anomalies.**
- Every detection has its own `spectrum_102_bands`, measured in `spectrum_epoch_id`: `epoch_a` if the source brightened, `epoch_b` if it faded.
- Detections within 12 PNG px of each other are merged; the highest SNR is kept.
- `anomaly_id` values are renumbered whenever the pipeline re-runs, so don't store them long-term.

**Other fields.**
- `flux_mjy_sr` is the average surface brightness in the aperture, in MJy/sr, after the local background is subtracted.
- `contamination_flags` uses SPHEREx's own flag names, such as `SOURCE`, `HOT`, `GHOST` and `PERSIST`. A flag is listed when at least half of the exposures covering that pixel have it.
- `chemical_filters` leaves out a molecule when an epoch has no exposures in its wavelength range.

**CORS.** The API, `/static/...` PNGs (served with `Access-Control-Allow-Origin: *` even without an `Origin` header) and Supabase public URLs can all be read from WebGL.

**Overlays.** Gaia and WISE catalogue overlays in the viewer are drawn by the frontend (Aladin Lite), not by this API. If you show a WISE image next to SPHEREx data, label which telescope each one comes from.

**UI rules the frontend must respect.**
- Show `cautions` and the `disclaimer` next to every chat answer, and `science_note` and `quality` whenever they aren't `null` (`low` = "lower limit, don't use to claim variability", `unvalidated` = "unvalidated data").
- If `saturated` is `true`, say so on the spectrum and light curve. Points with `flux_mjy_sr: null` are not drawn as 0, their `note` explains why.
- If `weak_signal` is `true`, warn that the motion is smaller than one SPHEREx pixel. If `fallback` is `true`, show `fallback_message`: it is not the data of the requested coordinates.
- Never present anything as a discovery. SIMBAD markers are "catalogued in SIMBAD", not detections, and must not be mixed with the software-assisted anomaly layer.
- For WISE 0855, RW Aurigae and R Draconis, show only light curves, brightness or the image, never the broadband subtraction or its "anomalies" (many are false).
- Don't branch on message text. Use `code`, `fallback_reason` and `stage`. Don't send scores to the game endpoints. On a 429, disable the button until `Retry-After` seconds pass.
- Keep a local copy of the loading phrases: the server may still be waking up.

**Hosting note.** If the API runs on a free hosting tier, the first request after a period of inactivity may take up to a couple of minutes while the server wakes up. Keep a local copy of the loading phrases and use the `cold_start` ones.

## Configuration

**Nothing is mandatory**: the API starts without any of these. This is what changes when they are missing.

| Variable | Default | If missing |
|---|---|---|
| `GROQ_API_KEY`, `DEEPSEEK_API_KEY` | unset | That provider is skipped. With neither, the chat answers only with ready-made replies (`provider: "canned"`) |
| `GROQ_MODEL`, `DEEPSEEK_MODEL` | `openai/gpt-oss-120b`, `deepseek-flash` | Defaults used |
| `CHAT_PROVIDER_TIMEOUT_S`, `CHAT_RATE_PER_MIN`, `CHAT_DAILY_PROVIDER_CAP`, `CHAT_CACHE_TTL_S` | `8`, `8`, `1000`, `3600` | Defaults used |
| `GAME_SESSIONS_PER_MIN`, `GAME_GUESSES_PER_MIN`, `GAME_FINISH_PER_MIN` | `10`, `60`, `5` | Per-IP limits |
| `GAME_SESSION_TTL_S`, `GAME_CROP_PX`, `GAME_SALT` | `900`, `300`, random per start | Salt fixes crop file names across restarts |
| `SIMBAD_TAP_URL` | CDS `sim-tap/sync` | Point it at the Harvard mirror (`https://simbad.cfa.harvard.edu/simbad/sim-tap/sync`) if CDS is down |
| `SIMBAD_TIMEOUT_S`, `SIMBAD_RATE_PER_MIN`, `SIMBAD_CACHE_TTL_S`, `SIMBAD_FETCH_LIMIT` | `5`, `20`, `3600`, `200` | Defaults used |
| `SUPABASE_URL`, `SUPABASE_KEY` | unset | Leaderboard and reports are kept in memory and lost on restart |
| `REPORTS_DEMO_SEED` | unset | The in-memory reports feed starts empty. `1` loads two labelled demo examples (`is_example: true`) |
| `PUBLIC_BASE` | unset | Absolute image URLs are built from each request's host |

AI keys go **only** on the backend, never in the browser. For local runs, `LIVE_WARMUP=0 uvicorn app.main:app --port 8000` skips the live warm-up.

## Status and known limits

Eskai is **deployed at [eskai.earth](https://eskai.earth)**. The backend notes below were written while testing locally (FastAPI test client and a local `uvicorn`), so read them as "not verified at hand-off time", and run the [smoke test](#smoke-test-a-deployment) against your deployment.

- **Not verified when the backend hand-off was written:** real calls to Groq and DeepSeek (no keys were available), the real Supabase tables, absolute image URLs behind the hosting proxy, the frontend against the newest backend changes, and the game's 503 error. Game crops are written to ephemeral disk and game sessions live in memory, so after a restart a game in progress answers 404.
- **Rate limits are approximate.** They are per IP, in memory and single-instance, and spoofing `X-Forwarded-For` can bypass them. The global daily chat cap still protects spending.
- **`time_ms` comes from the client** and can be faked. It is worth at most 200 of a round's 1,000 points.
- **Sometimes the object lands in the centre.** Each round places the object on one of 25 grid positions, one of them the centre, so about 1 round in 25 can be hit by clicking the middle.
- **Barnard's Star is very bright** in the crops, so its rounds are easier to spot.
- **SIMBAD:** verified against the Harvard mirror (the main CDS host was unreachable during testing). The real SIMBAD rate limit is unknown, so the server paces its own queries with a token bucket (burst 10, 3 per second on average).
- **Positions:** with SIMBAD proper motion, each object falls within 0.8 px of its image centre at the first visit, except R Draconis (7.4 px), probably because its stored position is rounded.

## Project layout

```
app/
  main.py               # FastAPI app, CORS, router wiring, startup cache warm-up
  core/config.py        # settings (CORS_ORIGINS, SUPABASE_URL/KEY env vars)
  core/targets.py       # the 5 targets: coordinates, astrometry, lookup boxes
  core/patch_geometry.py  # patch WCS, pixel conventions, duplicate suppression (pure Python)
  core/coords.py        # RA/Dec parser: degrees or sexagesimal (pure Python)
  core/urls.py          # relative image paths on disk <-> absolute URLs on the wire
  core/science_notes.py # per-object science notes and quality
  core/credits.py       # data credits, acknowledgement, software list
  core/loading_phrases.py  # waiting-screen phrases
  schemas/sky_patch.py  # Pydantic response contract
  schemas/reports.py    # citizen report contract
  routers/sky_patch.py  # HTTP layer
  routers/spectrum.py   # /spectrum: target + anomaly spectra
  routers/jobs.py       # POST /jobs, GET /jobs/{id}: jobs with progress
  routers/epochs.py     # /epochs (visit metadata) and /targets
  routers/chat.py       # /chat and /chat/suggestions
  routers/game.py       # /game/*: sessions, guess, finish, leaderboard
  routers/catalog_objects.py  # /catalog-objects
  routers/credits.py    # /credits
  routers/loading_phrases.py  # /loading-phrases (no dependencies, always answers)
  routers/reports.py    # /reports: submit, feed, verify
  routers/common.py     # coordinates, public URLs, live jobs + fallback
  services/annotations.py  # serve-time science caveats, quality and credits
  services/fallback.py  # offline-first: nearest cached patch when IRSA fails
  services/golden_path.py  # lookup over cached targets and spectra
  services/jobs.py      # thread-safe in-memory job store (TTL, de-dup, limits)
  services/live.py      # runs the pipeline for uncached positions
  services/chat.py      # prompt, cautions, cache, daily cap, canned answers
  services/chat_providers.py  # Groq and DeepSeek over async httpx
  services/game.py      # rounds, ground truth, crops, sessions, scoring
  services/game_scores.py  # leaderboard: Supabase or memory
  services/catalog_objects.py  # image, epoch, propagation, projection, ranking
  services/simbad.py    # SIMBAD TAP client, cache, pacing, fallbacks
  services/simbad_precomputed.py  # saved SIMBAD rows for the 5 objects
  services/rate_limit.py  # per-IP sliding window and global token bucket
  services/reports.py   # Supabase repo + in-memory fallback
  data/<target>.json    # generated Golden Path payloads
  static/golden_path/   # generated PNGs (--storage local)
scripts/generate_golden_path.py  # offline IRSA -> JSON/PNG pipeline
scripts/setup_supabase.py        # migration + bucket + optional PNG mirror
scripts/smoke_test.py            # checks a running API from the outside
scripts/export_contract_example.py  # regenerates docs/contract-example.json
scripts/generate_simbad_golden.py  # precomputed SIMBAD lists for the 5 objects
supabase/migrations/    # SQL for citizen_reports (table, indexes, RLS, verify RPC)
supabase/game_scores.sql  # game leaderboard table (run once in the SQL Editor)
app/data/simbad/        # saved SIMBAD data for the 5 objects (used by chat and fallback)
app/static/game/        # generated game crops (ephemeral on free hosting)
docs/                   # contract example, wow-contract.md, frontend-handoff.md
tests/                  # includes tests/test_game.py
```

### Adding a target

Add a `Target(...)` to `app/core/targets.py`, then run the pipeline for it. The API serves every catalogue target that has a JSON file, and checks each file against the schema at startup.

## Data used (Datos utilizados)

Everything the API serves comes from public archives. Nothing is simulated: where a measurement is impossible (e.g. saturated stars), the response says so (`null` + `note`, `saturated`, `spectrum_message`). The same citations, word for word, come from `GET /credits` ([`app/core/credits.py`](app/core/credits.py)); `tests/test_credits.py` checks that the two agree. `/sky-patch` and `/spectrum` responses carry the first four in `data_credits`.

| Source | What we use it for | Access and citation |
|---|---|---|
| **SPHEREx (NASA/JPL/Caltech), served by IRSA/IPAC**: SPHEREx Data Release 1 (`spherex_dr1`) and Quick Release 3 (`spherex_qr3`) | All images, difference images, spectra and light curves: Level 2 Spectral Images (MEF), downloaded as server-side cutouts | IRSA SIA2 https://irsa.ipac.caltech.edu/SIA; DR1 DOI [10.26131/IRSA678](https://www.ipac.caltech.edu/doi/10.26131/IRSA678), QR3 DOI [10.26131/IRSA662](https://www.ipac.caltech.edu/doi/10.26131/IRSA662); deep fields `spherex_dr1_deep` / `spherex_qr3_deep` only as a fallback. [SPHEREx at IRSA](https://irsa.ipac.caltech.edu/Missions/spherex.html), [archive user guide](https://caltech-ipac.github.io/spherex-archive-documentation/) |
| **Gaia DR3 (ESA)** | Reference positions and proper motions of Barnard's Star, Epsilon Indi B, RW Aurigae and R Draconis, propagated to each SPHEREx exposure date; in the spot-the-mover game, the candidate stars and the correct answer of every round | [Gaia archive](https://gea.esac.esa.int/archive/); Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1. The game's pool builder queries the ARI Heidelberg mirror, https://gaia.ari.uni-heidelberg.de/tap |
| **WISE (NASA)** | Reference position of WISE 0855-0714 (from its WISE designation, epoch ~2010.4); proper motion from Luhman & Esplin (2016, AJ 152, 78) | [WISE at IRSA](https://irsa.ipac.caltech.edu/Missions/wise.html); Luhman & Esplin (2016, AJ 152, 78) |
| **SIMBAD astronomical database, CDS, Strasbourg Astronomical Observatory, France** | Catalogued objects (identifier, type, position, proper motion, magnitude, bibliographic references) marked on the SPHEREx images, with positions propagated to the observation date; the star names of the spot-the-mover game's WISE vs SPHEREx rounds. Labelled as *catalogued in SIMBAD*, never as detected by SPHEREx | [SIMBAD](https://simbad.cds.unistra.fr/simbad/), TAP service https://simbad.cds.unistra.fr/simbad/sim-tap (the Harvard mirror is an alternative); Wenger et al. 2000, A&AS 143, 9, DOI [10.1051/aas:2000332](https://doi.org/10.1051/aas:2000332) |
| **SPHEREx DR1 HiPS** (SPHEREx (NASA/JPL/Caltech), served by IRSA/IPAC): SPHEREx Data Release 1 HiPS spectral cube (hips5_cube), AWS Open Data bucket nasa-irsa-spherex | SPHEREx images of the spot-the-mover game's WISE vs SPHEREx rounds: the 1.1-2.4 um channels of the DR1 HiPS cube, read by HTTP Range requests | https://nasa-irsa-spherex.s3.amazonaws.com/dr1/hips5_cube/ ([IRSA cloud access](https://irsa.ipac.caltech.edu/cloud_access/)); SPHEREx DR1 DOI [10.26131/IRSA678](https://www.ipac.caltech.edu/doi/10.26131/IRSA678) |
| **unWISE** (unWISE / NASA/JPL-Caltech / D. Lang (Perimeter Institute)) | WISE (2010) images of the spot-the-mover game's WISE vs SPHEREx rounds: W1 (3.4 um) cutouts | [unWISE](https://unwise.me/), cutout service https://unwise.me/cutout_fits; Lang 2014, AJ 147, 108. unwise.me asks users to cite this paper and states no licence for the coadd data: its CC BY-NC-SA 4.0 notice captions only the showcase images of its home page (checked 2026-11-15). The game PNGs in `app/static/game_pool/` are derived from these cutouts and carry the same citation |

SPHEREx acknowledgement, as requested by IRSA ([source](https://irsa.ipac.caltech.edu/Missions/spherex.html)):

> This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and Space Administration.

SIMBAD acknowledgement: "This research has made use of the SIMBAD database, operated at CDS, Strasbourg, France".

Canonical SPHEREx papers listed by IRSA: Bock et al. (2026, mission), Akeson et al. (2026, pipeline), Crill et al. (2025, sky simulator), Korngut et al. (2026, instrument & calibration).

Software: FastAPI, Astropy, Astroquery/PyVO, reproject, astropy-healpix, NumPy, SciPy, OpenCV, Matplotlib, Pillow.

## About this project

Built for the **NASA Space Apps Challenge 2026** and live at [eskai.earth](https://eskai.earth). It is an independent project and is not endorsed by NASA. It uses public SPHEREx data and is inspired by the long tradition of blink-comparison astronomy, from Clyde Tombaugh's 1930 discovery of Pluto to modern citizen-science projects that hunt for moving objects in survey images.

## License

MIT, see [LICENSE](LICENSE). (TODO: copyright holder name pending.)
