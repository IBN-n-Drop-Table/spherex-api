#!/usr/bin/env python3
"""Smoke test of a deployed spherex-api. Standard library only.

    python scripts/smoke_test.py https://spherex-api.onrender.com
    python scripts/smoke_test.py http://127.0.0.1:8000 --skip-live

Checks /health, the 5 challenge objects on /sky-patch and /spectrum, /credits,
that every image URL uses the scheme and host of the base URL (https on a
public deploy), CORS on a PNG and on a preflight, the error format, a lost job,
and a real POST /jobs polled until it finishes. Prints OK / FALLO per check;
exits 1 if anything failed. A free Render instance may take ~1 min to wake up:
the first request waits up to --wake-timeout seconds.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlencode, urlsplit

TARGETS = ["WISE_0855", "BARNARDS_STAR", "EPSILON_INDI_B", "RW_AURIGAE", "R_DRACONIS"]
DOIS = {"10.26131/IRSA678", "10.26131/IRSA662"}
ORIGIN = "https://smoke-test.example"


class Report:
    def __init__(self) -> None:
        self.failed = 0
        self.passed = 0

    def check(self, ok: bool, name: str, detail: str = "") -> bool:
        print(f"{'OK   ' if ok else 'FALLO'} {name}{' - ' + detail if detail else ''}", flush=True)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        return ok

    def note(self, text: str) -> None:
        print(f"      {text}", flush=True)


def request(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None,
            timeout: float = 60.0) -> tuple[int, dict, bytes, float]:
    """(status, headers lower-cased, body bytes, seconds). Never raises: an HTTP error
    returns its status; a network error (DNS, refused, timeout) returns status 0 and
    the error text as the body, so the check reports FALLO instead of crashing."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, {k.lower(): v for k, v in res.headers.items()}, res.read(), time.monotonic() - t0
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read(), time.monotonic() - t0
    except (urllib.error.URLError, OSError) as exc:  # includes TimeoutError, ConnectionError
        reason = getattr(exc, "reason", exc)
        return 0, {}, f"network error: {reason}".encode(), time.monotonic() - t0


def as_json(raw: bytes) -> dict | list | None:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def image_urls(patch: dict) -> list[str]:
    urls = []
    for e in patch.get("timelapse_epochs", []):
        urls.append(e["image_url"])
        urls += list(e.get("chemical_filters", {}).values())
    diff = patch.get("difference_imaging", {})
    urls.append(diff.get("subtracted_image_url"))
    urls += [p["subtracted_image_url"] for p in diff.get("pairwise") or []]
    return [u for u in urls if u]


def check_urls(r: Report, name: str, urls: list[str], base: str) -> None:
    want = urlsplit(base)
    bad = [u for u in urls if (urlsplit(u).scheme, urlsplit(u).netloc) != (want.scheme, want.netloc)]
    r.check(not bad, f"{name}: {len(urls)} URL(s) use {want.scheme}://{want.netloc}",
            f"e.g. {bad[0]}" if bad else "")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("base", help="API origin, e.g. https://spherex-api.onrender.com")
    p.add_argument("--skip-live", action="store_true", help="don't run the live POST /jobs check")
    p.add_argument("--ra", default="10h00m28s", help="live job RA (default: a COSMOS field, not cached)")
    p.add_argument("--dec", default="+02d12m21s")
    p.add_argument("--fov", type=float, default=2.0, help="live job fov in arcmin (default 2: fast)")
    p.add_argument("--job-timeout", type=float, default=600.0, help="max seconds to wait for the live job")
    p.add_argument("--wake-timeout", type=float, default=120.0, help="first request timeout (cold start)")
    args = p.parse_args()
    base = args.base.rstrip("/")
    api = f"{base}/api/v1"
    r = Report()
    print(f"spherex-api smoke test against {base}\n")
    if urlsplit(base).scheme != "https":
        r.note("AVISO: the base URL is not https; a public deploy should be.")

    # /health (may wake a sleeping free instance)
    status, _, raw, dt = request(f"{base}/health", timeout=args.wake_timeout)
    health = as_json(raw) or {}
    r.check(status == 200 and health.get("status") == "ok", "/health",
            f"{status} in {dt:.1f}s, {health or raw.decode(errors='replace')[:160]}")
    if status == 0:  # nothing answers: no point in going on
        print(f"\n{r.passed} OK, {r.failed} FALLO")
        return 1

    # The 5 challenge objects
    first_png = None
    for key in TARGETS:
        status, _, raw, dt = request(f"{api}/sky-patch?{urlencode({'target': key})}")
        body = as_json(raw) or {}
        ok = status == 200 and body.get("fallback") is False and bool(body.get("data_credits"))
        r.check(ok, f"/sky-patch {key}", f"{status} in {dt * 1000:.0f} ms"
                + ("" if ok else f", fallback={body.get('fallback')}, credits={bool(body.get('data_credits'))}"))
        if status == 200:
            urls = image_urls(body)
            check_urls(r, f"/sky-patch {key}", urls, base)
            first_png = first_png or (urls[0] if urls else None)
        status, _, raw, dt = request(f"{api}/spectrum?{urlencode({'target': key})}")
        body = as_json(raw) or {}
        r.check(status == 200 and bool(body.get("data_credits")), f"/spectrum {key}",
                f"{status} in {dt * 1000:.0f} ms, {len(body.get('spectrum_102_bands') or [])} bands")

    # /credits
    status, _, raw, _ = request(f"{base}/credits")
    body = as_json(raw) or {}
    dois = {c.get("doi") for c in body.get("data_credits", [])}
    r.check(status == 200 and DOIS <= dois and bool(body.get("acknowledgement")), "/credits",
            f"{status}, DOIs {sorted(d for d in dois if d)}")

    # CORS
    if first_png:
        status, hdrs, data, _ = request(first_png, headers={"Origin": ORIGIN, "Accept": "image/png"})
        r.check(status == 200 and hdrs.get("content-type") == "image/png"
                and hdrs.get("access-control-allow-origin") in ("*", ORIGIN) and data[:8] == b"\x89PNG\r\n\x1a\n",
                "CORS on a PNG", f"{status} {hdrs.get('content-type')} ACAO={hdrs.get('access-control-allow-origin')}"
                + (f" ({data.decode(errors='replace')[:120]})" if status == 0 else ""))
    else:
        r.check(False, "CORS on a PNG", "no image URL to test")
    status, hdrs, _, _ = request(f"{api}/jobs", method="OPTIONS", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    r.check(status == 200 and hdrs.get("access-control-allow-origin") in ("*", ORIGIN), "CORS preflight POST /jobs",
            f"{status} ACAO={hdrs.get('access-control-allow-origin')}")

    # Error format
    status, _, raw, _ = request(f"{api}/sky-patch?target=WISE_0855&fov=99")
    body = as_json(raw) or {}
    r.check(status == 422 and body.get("code") == "validation_error" and isinstance(body.get("detail"), str),
            "error format (422)", f"{status} {body.get('code')}")
    status, _, raw, _ = request(f"{api}/jobs/{uuid.uuid4()}")
    body = as_json(raw) or {}
    r.check(status == 404 and body.get("code") == "job_not_found", "lost job -> job_not_found",
            f"{status} {body.get('code')}: {body.get('detail')}")

    # Live job with polling
    if args.skip_live:
        r.note("live POST /jobs skipped (--skip-live)")
    else:
        t0 = time.monotonic()
        status, hdrs, raw, _ = request(f"{api}/jobs", method="POST",
                                       body={"ra": args.ra, "dec": args.dec, "fov": args.fov})
        job = as_json(raw) or {}
        if r.check(status in (200, 202) and "job_id" in job, "POST /jobs (live)",
                   f"{status} job {job.get('job_id')} Location={hdrs.get('location')}"):
            location = hdrs.get("location") or f"{api}/jobs/{job['job_id']}"
            if hdrs.get("location"):
                check_urls(r, "Location header", [location], base)
            last_stage = None
            while job.get("status") in ("queued", "running") and time.monotonic() - t0 < args.job_timeout:
                time.sleep(max(1.0, (job.get("retry_after_ms") or 1500) / 1000))
                status, _, raw, _ = request(location)
                job = as_json(raw) or {}
                if status != 200:
                    break
                if job.get("stage") != last_stage:
                    last_stage = job.get("stage")
                    r.note(f"t={time.monotonic() - t0:5.0f}s {job.get('status')} {last_stage} "
                           f"{job.get('progress')} {job.get('message') or ''}"[:140])
            result = job.get("result") or {}
            ok = job.get("status") == "completed"
            detail = f"{job.get('status')} in {time.monotonic() - t0:.0f}s"
            if result.get("fallback"):
                detail += f" with FALLBACK ({result.get('fallback_reason')}): {result.get('fallback_message')}"
            elif ok:
                detail += f", {len(result.get('timelapse_epochs', []))} visits, " \
                          f"{len(result.get('difference_imaging', {}).get('pairwise') or [])} pairs"
            if job.get("status") == "failed":
                detail += f": {job.get('error')}"
            r.check(ok and not result.get("fallback"), "live job completed", detail)
            if ok:
                check_urls(r, "live job result", image_urls(result), base)

    print(f"\n{r.passed} OK, {r.failed} FALLO")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
