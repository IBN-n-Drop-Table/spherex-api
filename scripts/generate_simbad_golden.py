#!/usr/bin/env python3
"""Precompute the SIMBAD objects of the 5 Golden Path patches: app/data/simbad/<target>.json.

The API serves these files when SIMBAD does not answer (GET /api/v1/catalog-objects,
`source: "precomputed"`). Each file holds the UNFILTERED J2000 rows of the cone search around the
patch (with the cone widened for proper motion up to the last visit), so any `type_filter`, `max_n`
and `epoch` can be served from it. Positions are propagated and projected at request time, never stored.

    python scripts/generate_simbad_golden.py                      # all 5 targets
    python scripts/generate_simbad_golden.py --target BARNARDS_STAR
    python scripts/generate_simbad_golden.py --dry-run            # print the ADQL, no network
    python scripts/generate_simbad_golden.py --check-otypes       # are our otype codes known to SIMBAD?
    python scripts/generate_simbad_golden.py --tap-url https://simbad.cfa.harvard.edu/simbad/sim-tap/sync

Needs the network, and nothing from requirements-science.txt. A target is written only if its query
succeeds, so a failed run never leaves a half-written file. As a sanity check it reports, per target,
where the target itself shows up on the epoch-1 image: it must be within a few pixels of the centre.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.simbad_otypes import COMMON_STAR_OTYPES, IR_GALAXY_OTYPES  # noqa: E402
from app.core.simbad_settings import SimbadSettings  # noqa: E402
from app.core.sky_projection import years_since_j2000  # noqa: E402
from app.core.targets import CATALOG  # noqa: E402
from app.services.catalog_objects import image_geometry, place  # noqa: E402
from app.services.golden_path import cached_target  # noqa: E402
from app.services.simbad import (  # noqa: E402
    PAD_DEG, SimbadService, SimbadUnavailable, build_adql, http_transport, parse_tap_json, quantize_years,
    rows_to_dicts, round_center,
)
from app.services.simbad_precomputed import precomputed_path  # noqa: E402

PAUSE_S = 1.0  # between queries: well under SIMBAD's ~5 queries/s blocking threshold
CENTRE_TOLERANCE_PX = 5.0


def target_plan(key: str) -> dict:
    """Centre, radius and widening of the query for one cached target (no network)."""
    patch = cached_target(CATALOG[key])
    if patch is None:
        raise SystemExit(f"{key}: no cached patch in app/data, nothing to precompute")
    geo = image_geometry(key, None, None, 5.0, None)
    years = quantize_years(max(years_since_j2000(e.date) for e in patch.timelapse_epochs))
    ra, dec = round_center(*geo.center)
    return {"geo": geo, "years": years, "ra": ra, "dec": dec, "radius": round(geo.radius_deg, 3), "patch": patch}


def generate(key: str, cfg: SimbadSettings, transport=http_transport, now: datetime | None = None) -> tuple[dict, list[str]]:
    """The file content for one target and a list of warnings. Raises SimbadUnavailable on failure."""
    plan = target_plan(key)
    adql = build_adql(plan["ra"], plan["dec"], plan["radius"] + PAD_DEG, "all", cfg.fetch_limit, plan["years"])
    rows = parse_tap_json(transport(cfg.tap_url, adql, max(cfg.timeout_s, 30.0)))
    warnings: list[str] = []
    if len(rows) >= cfg.fetch_limit:
        warnings.append(f"{key}: SIMBAD returned {len(rows)} rows (the limit): the least-cited were cut off")
    # sanity: the target must be on the image centre at epoch 1 (the WCS is centred on it)
    best = None
    for r in rows:
        o = place(r, plan["geo"])
        if o is not None:
            d = math.dist((o.x, o.y), (plan["geo"].width / 2, plan["geo"].height / 2))
            if best is None or d < best[0]:
                best = (d, r.main_id)
    if best is None or best[0] > CENTRE_TOLERANCE_PX:
        warnings.append(f"{key}: no SIMBAD object lands within {CENTRE_TOLERANCE_PX:g} px of the image centre"
                        + ("" if best is None else f" (closest: {best[1]!r} at {best[0]:.1f} px)"))
    else:
        warnings.append(f"{key}: OK, {best[1]!r} lands {best[0]:.2f} px from the image centre")
    data = {
        "generated": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tap_url": cfg.tap_url, "target": key,
        "query": {"ra": plan["ra"], "dec": plan["dec"], "radius_deg": plan["radius"] + PAD_DEG,
                  "years_since_j2000_max": plan["years"], "type_filter": "all", "adql": adql},
        "objects": rows_to_dicts(rows),
    }
    return data, warnings


def check_otypes(cfg: SimbadSettings, transport=http_transport) -> dict[str, list[str]]:
    """Codes used by our type groups that SIMBAD's own `otypedef` table does not list."""
    known = {r[0] for r in transport(cfg.tap_url, "SELECT otype FROM otypedef", 30.0)["data"]}
    return {"no_common_stars": [c for c in COMMON_STAR_OTYPES if c not in known],
            "ir_galaxies": [c for c in IR_GALAXY_OTYPES if c not in known]}


def main(argv: list[str] | None = None, transport=http_transport) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--target", action="append", choices=list(CATALOG), help="default: all five")
    p.add_argument("--tap-url", help="override SIMBAD_TAP_URL")
    p.add_argument("--out-dir", type=Path, default=settings.data_dir)
    p.add_argument("--dry-run", action="store_true", help="print the ADQL queries and exit")
    p.add_argument("--check-otypes", action="store_true", help="compare our otype codes with SIMBAD's otypedef")
    args = p.parse_args(argv)

    cfg = SimbadSettings()
    if args.tap_url:
        cfg = SimbadSettings(tap_url=args.tap_url)
    keys = args.target or list(CATALOG)

    if args.dry_run:
        for key in keys:
            plan = target_plan(key)
            print(f"-- {key}\n" + build_adql(plan["ra"], plan["dec"], plan["radius"] + PAD_DEG, "all",
                                             cfg.fetch_limit, plan["years"]) + "\n")
        return 0
    try:
        if args.check_otypes:
            unknown = check_otypes(cfg, transport)
            for group, codes in unknown.items():
                print(f"{group}: {'all codes known to SIMBAD' if not codes else 'UNKNOWN to SIMBAD: ' + ', '.join(codes)}")
            return 1 if any(unknown.values()) else 0
        failed = 0
        for i, key in enumerate(keys):
            if i:
                time.sleep(PAUSE_S)
            try:
                data, warnings = generate(key, cfg, transport)
            except SimbadUnavailable as exc:
                print(f"{key}: FAILED ({exc}); nothing written", file=sys.stderr)
                failed += 1
                continue
            for w in warnings:
                print(w)
            path = precomputed_path(key, args.out_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", "utf-8")
            print(f"{key}: {len(data['objects'])} objects -> {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}")
        return 1 if failed else 0
    except SimbadUnavailable as exc:
        print(f"SIMBAD unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
