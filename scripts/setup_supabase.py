#!/usr/bin/env python3
"""One-shot, idempotent Supabase setup + Golden Path publishing.

Run it after `scripts/generate_golden_path.py`; it does NOT re-run the FITS
pipeline. Steps:

  1. Database: apply supabase/migrations/20261114000000_citizen_reports.sql
     through SUPABASE_DB_URL (needs psycopg or psycopg2). Otherwise check
     that `citizen_reports` is reachable and, if not, print how to apply the
     SQL by hand.
  2. Storage: create the public bucket SUPABASE_BUCKET if needed, then upload
     every app/static/golden_path/*.png to golden_path/<file> (upsert).
  3. JSON: rewrite local/bucket image URLs in app/data/*.json to the public
     Supabase URLs, validate against SkyPatchResponse, write back (indent=2).

Re-running is safe: uploads upsert, and JSON files are written only when
their content changes.

    .venv/bin/python scripts/setup_supabase.py --dry-run   # no network, no writes
    .venv/bin/python scripts/setup_supabase.py
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from app.schemas.sky_patch import SkyPatchResponse  # noqa: E402

MIGRATION = REPO_ROOT / "supabase" / "migrations" / "20261114000000_citizen_reports.sql"
PNG_DIR = REPO_ROOT / "app" / "static" / "golden_path"
DATA_DIR = REPO_ROOT / "app" / "data"
REPORTS_TABLE = "citizen_reports"
STORAGE_PREFIX = "golden_path"
DEFAULT_BUCKET = "spherex-golden-path"
NIL_UUID = "00000000-0000-0000-0000-000000000000"

# URL paths we own and may rewrite: the API's static mount and any public bucket.
REWRITABLE_PATH_MARKERS = ("/static/golden_path/", "/storage/v1/object/public/")


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
def step(title: str) -> None:
    print(f"\n== {title}")


def ok(msg: str) -> None:
    print(f"  [ok]   {msg}")


def info(msg: str) -> None:
    print(f"  [..]   {msg}")


def warn(msg: str) -> None:
    print(f"  [warn] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE, `export`, quotes, # comments."""
    values: dict[str, str] = {}
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def load_env(path: Path) -> str:
    """Load `path` into os.environ without overriding variables already set."""
    if not path.is_file():
        return f"no {path.name} found, using the process environment"
    try:
        from dotenv import load_dotenv
    except ImportError:
        for key, value in _parse_env_file(path).items():
            os.environ.setdefault(key, value)
        return f"loaded {path.name} (stdlib parser)"
    load_dotenv(path, override=False)
    return f"loaded {path.name} (python-dotenv)"


@dataclass(frozen=True)
class Config:
    url: str | None
    key: str | None
    key_source: str
    bucket: str
    db_url: str | None

    @classmethod
    def from_env(cls) -> Config:
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or None
        return cls(
            url=(os.getenv("SUPABASE_URL") or "").rstrip("/") or None,
            key=service_key or os.getenv("SUPABASE_KEY") or None,
            key_source="SUPABASE_SERVICE_ROLE_KEY" if service_key else "SUPABASE_KEY",
            bucket=os.getenv("SUPABASE_BUCKET") or DEFAULT_BUCKET,
            db_url=os.getenv("SUPABASE_DB_URL") or None,
        )

    def public_url(self, filename: str) -> str:
        """Same URL that storage3's get_public_url() builds (used for --dry-run)."""
        base = self.url or "https://your-project.supabase.co"  # placeholder must be a valid host
        return f"{base}/storage/v1/object/public/{self.bucket}/{STORAGE_PREFIX}/{filename}"


def key_role(key: str) -> str:
    """Best-effort role of a Supabase API key, without verifying it."""
    if key.startswith("sb_secret_"):
        return "service_role"
    if key.startswith("sb_publishable_"):
        return "anon"
    try:
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("role", "unknown"))
    except (IndexError, ValueError):
        return "unknown"


# --------------------------------------------------------------------------- #
# Step 1: database
# --------------------------------------------------------------------------- #
def _execute_sql(db_url: str, sql: str) -> str | None:
    """Run the whole file in one round-trip. Returns the driver name, or None
    if no driver is installed. Without params, both drivers send a simple
    query, so multiple statements and $$ bodies are fine (and atomic)."""
    reload_schema = "notify pgrst, 'reload schema'"  # make PostgREST see the new table now
    try:
        import psycopg
    except ImportError:
        pass
    else:
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(sql)
            conn.execute(reload_schema)
        return "psycopg"
    try:
        import psycopg2
    except ImportError:
        return None
    conn = psycopg2.connect(db_url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(reload_schema)
    finally:
        conn.close()
    return "psycopg2"


def _reports_reachable(client: Any) -> tuple[bool, str]:
    try:
        client.table(REPORTS_TABLE).select("id").limit(1).execute()
        # Also proves the RPC exists; a nil UUID matches no row, so nothing changes.
        client.rpc("verify_citizen_report", {"report_id": NIL_UUID}).execute()
    except Exception as exc:
        return False, getattr(exc, "message", None) or repr(exc)
    return True, ""


def setup_database(cfg: Config, client: Any | None, dry_run: bool) -> bool:
    step("1/3 Database: citizen_reports")
    if not MIGRATION.is_file():
        fail(f"migration not found: {MIGRATION.relative_to(REPO_ROOT)}")
        return False
    rel = MIGRATION.relative_to(REPO_ROOT)

    if dry_run:
        how = "via SUPABASE_DB_URL" if cfg.db_url else "check table via the Supabase client"
        info(f"would apply {rel} ({how})")
        return True

    if cfg.db_url:
        try:
            driver = _execute_sql(cfg.db_url, MIGRATION.read_text("utf-8"))
        except Exception as exc:
            warn(f"could not apply migration through SUPABASE_DB_URL: {exc}")
        else:
            if driver:
                ok(f"applied {rel} with {driver}")
            else:
                info("SUPABASE_DB_URL is set but no driver found (pip install 'psycopg[binary]')")

    reachable, reason = _reports_reachable(client)
    if reachable:
        ok(f"table public.{REPORTS_TABLE} and verify_citizen_report() are reachable")
        return True

    warn(f"public.{REPORTS_TABLE} is not reachable yet ({reason})")
    print(
        f"""
         Apply the migration once (safe to re-run):
           1. Supabase Dashboard -> SQL Editor -> New query
           2. Paste the contents of {rel}
           3. Click Run, then re-run this script.
         Until then the API keeps citizen reports in memory.
"""
    )
    return False


# --------------------------------------------------------------------------- #
# Step 2: storage
# --------------------------------------------------------------------------- #
@dataclass
class UploadResult:
    urls: dict[str, str] = field(default_factory=dict)  # filename -> public URL
    failed: dict[str, str] = field(default_factory=dict)  # filename -> error


def ensure_bucket(client: Any, bucket: str) -> None:
    existing = {b.id: b for b in client.storage.list_buckets()}
    current = existing.get(bucket)
    if current is None:
        client.storage.create_bucket(bucket, options={"public": True})
        ok(f"created public bucket '{bucket}'")
    elif not current.public:
        client.storage.update_bucket(bucket, {"public": True})
        ok(f"bucket '{bucket}' existed but was private: made it public")
    else:
        ok(f"bucket '{bucket}' exists and is public")


def upload_pngs(cfg: Config, client: Any | None, pngs: list[Path], dry_run: bool) -> UploadResult:
    step(f"2/3 Storage: {len(pngs)} PNG(s) -> bucket '{cfg.bucket}'/{STORAGE_PREFIX}/")
    result = UploadResult()
    if not pngs:
        warn(f"no PNGs in {PNG_DIR.relative_to(REPO_ROOT)}; run generate_golden_path.py first")
        return result

    if dry_run:
        for png in pngs:
            result.urls[png.name] = cfg.public_url(png.name)
            info(f"would upload {png.name} ({png.stat().st_size / 1024:.0f} KiB)")
        return result

    try:
        ensure_bucket(client, cfg.bucket)
    except Exception as exc:
        reason = getattr(exc, "message", None) or repr(exc)
        fail(f"cannot prepare bucket '{cfg.bucket}': {reason}")
        result.failed = {png.name: reason for png in pngs}
        return result
    bucket = client.storage.from_(cfg.bucket)
    for png in pngs:
        path = f"{STORAGE_PREFIX}/{png.name}"
        try:
            bucket.upload(
                path=path,
                file=png.read_bytes(),
                file_options={"content-type": "image/png", "upsert": "true", "cache-control": "300"},
            )
        except Exception as exc:
            result.failed[png.name] = getattr(exc, "message", None) or repr(exc)
            fail(f"{png.name}: {result.failed[png.name]}")
            continue
        result.urls[png.name] = bucket.get_public_url(path).rstrip("?")
        ok(png.name)
    return result


# --------------------------------------------------------------------------- #
# Step 3: JSON patching
# --------------------------------------------------------------------------- #
@dataclass
class PatchResult:
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    missing_pngs: dict[str, set[str]] = field(default_factory=dict)  # file -> PNG names


def _rewrite(url: str, urls: dict[str, str], missing: set[str]) -> str:
    path = urlsplit(url).path
    if not any(marker in path for marker in REWRITABLE_PATH_MARKERS):
        return url  # someone else's URL: leave it alone
    filename = path.rsplit("/", 1)[-1]
    if filename not in urls:
        missing.add(filename)
        return url
    return urls[filename]


def patch_payload(raw: dict[str, Any], urls: dict[str, str]) -> tuple[dict[str, Any], set[str]]:
    """Return a patched copy of a sky-patch payload and the PNGs it lacks."""
    data = json.loads(json.dumps(raw))  # deep copy
    missing: set[str] = set()
    for epoch in data.get("timelapse_epochs", []):
        epoch["image_url"] = _rewrite(epoch["image_url"], urls, missing)
        filters = epoch.get("chemical_filters", {})
        for molecule, url in filters.items():
            filters[molecule] = _rewrite(url, urls, missing)
    diff = data.get("difference_imaging", {})
    if "subtracted_image_url" in diff:
        diff["subtracted_image_url"] = _rewrite(diff["subtracted_image_url"], urls, missing)
    return data, missing


def _render(model: SkyPatchResponse) -> str:
    # Same formatting as generate_golden_path.py, so diffs stay minimal.
    return json.dumps(model.model_dump(mode="json"), indent=2) + "\n"


def load_and_validate(files: list[Path]) -> dict[Path, dict[str, Any]] | None:
    """Validate every JSON before touching Supabase. None if any is broken."""
    loaded: dict[Path, dict[str, Any]] = {}
    broken = False
    for path in files:
        try:
            raw = json.loads(path.read_text("utf-8"))
            SkyPatchResponse.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            fail(f"{path.name} does not match SkyPatchResponse:\n{exc}")
            broken = True
            continue
        loaded[path] = raw
    return None if broken else loaded


def patch_json_files(
    loaded: dict[Path, dict[str, Any]], urls: dict[str, str], dry_run: bool
) -> PatchResult:
    step(f"3/3 JSON: {len(loaded)} file(s) in {DATA_DIR.relative_to(REPO_ROOT)}")
    result = PatchResult()
    for path, raw in loaded.items():
        patched, missing = patch_payload(raw, urls)
        if missing:
            result.missing_pngs[path.name] = missing
            warn(f"{path.name}: no uploaded PNG for {', '.join(sorted(missing))} (URL kept)")
        try:
            model = SkyPatchResponse.model_validate(patched)
        except ValidationError as exc:
            result.invalid.append(path.name)
            fail(f"{path.name}: patched payload is invalid, not written:\n{exc}")
            continue

        new_text = _render(model)
        if new_text == path.read_text("utf-8"):
            result.unchanged.append(path.name)
            ok(f"{path.name}: no changes needed")
            continue

        result.updated.append(path.name)
        example = model.difference_imaging.subtracted_image_url
        if dry_run:
            info(f"{path.name}: would update URLs (e.g. subtracted_image_url -> {example})")
        else:
            path.write_text(new_text, "utf-8")
            ok(f"{path.name}: URLs updated and validated")
    return result


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def make_client(cfg: Config) -> Any:
    try:
        from supabase import create_client
    except ImportError:
        sys.exit("The supabase package is missing: .venv/bin/pip install -r requirements.txt")
    return create_client(cfg.url, cfg.key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate files and show the plan; no Supabase calls, no writes",
    )
    parser.add_argument(
        "--env-file", type=Path, default=REPO_ROOT / ".env", help="default: %(default)s"
    )
    args = parser.parse_args(argv)

    print(f"SPHEREx Supabase setup{' (dry run)' if args.dry_run else ''}")
    info(load_env(args.env_file))
    cfg = Config.from_env()

    client = None
    if not args.dry_run:
        missing = [name for name, value in (("SUPABASE_URL", cfg.url), ("SUPABASE_KEY", cfg.key)) if not value]
        if missing:
            fail(f"missing {', '.join(missing)}. Copy .env.example to .env and fill it in.")
            return 2
        role = key_role(cfg.key)
        if role != "service_role":
            warn(
                f"{cfg.key_source} looks like a '{role}' key. Creating buckets and uploading "
                "need the service_role key (Dashboard -> Project Settings -> API Keys)."
            )
        client = make_client(cfg)
    info(f"project: {cfg.url or '(SUPABASE_URL not set)'}  bucket: {cfg.bucket}")

    json_files = sorted(DATA_DIR.glob("*.json"))
    loaded = load_and_validate(json_files)
    if loaded is None:
        fail("fix the JSON files above first (nothing was uploaded)")
        return 1
    ok(f"{len(loaded)} JSON file(s) match SkyPatchResponse")

    db_ready = setup_database(cfg, client, args.dry_run)
    uploads = upload_pngs(cfg, client, sorted(PNG_DIR.glob("*.png")), args.dry_run)
    patched = patch_json_files(loaded, uploads.urls, args.dry_run)

    would = " (would)" if args.dry_run else ""
    step("Summary")

    def row(label: str, value: str) -> None:
        print(f"  {label:<22} {value}")

    failed = f"  (failed: {len(uploads.failed)})" if uploads.failed else ""
    row(f"PNGs uploaded{would}", f"{len(uploads.urls)}{failed}")
    row(f"JSON updated{would}", f"{len(patched.updated)}  {', '.join(patched.updated)}")
    row("JSON unchanged", f"{len(patched.unchanged)}  {', '.join(patched.unchanged)}")
    if patched.invalid:
        row("JSON invalid", ", ".join(patched.invalid))
    if patched.missing_pngs:
        row("JSON w/ local URLs", f"{', '.join(sorted(patched.missing_pngs))} (PNG not uploaded)")
    if args.dry_run:
        row("Database", "not checked (dry run)")
    else:
        row("Database", "citizen_reports ready" if db_ready else "migration pending: reports use in-memory fallback")

    hard_errors = bool(uploads.failed or patched.invalid)
    if args.dry_run:
        print("\nDry run finished. Run without --dry-run to apply.")
    elif hard_errors:
        print("\nNOT READY: fix the failures above and re-run (the script is idempotent).")
    else:
        print(
            "\nREADY. Start the API with:\n"
            "  .venv/bin/uvicorn app.main:app --reload --port 8000 --env-file .env"
        )
    return 1 if hard_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
