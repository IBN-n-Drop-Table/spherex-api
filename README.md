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
  schemas/sky_patch.py  # Pydantic response contract
  routers/sky_patch.py  # HTTP layer
  services/golden_path.py  # target registry + lookup
  data/wise_0855.json   # cached contract for WISE 0855
tests/
```

## Adding another Golden Path target

1. Save its response JSON as `app/data/<target>.json`.
2. Add a `GoldenPathTarget(...)` entry (key, file, RA/Dec box) to `TARGETS` in `app/services/golden_path.py`.

Every file is checked against the schema when the app starts. A malformed fixture stops the server at startup, before the demo.
