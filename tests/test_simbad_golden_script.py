"""scripts/generate_simbad_golden.py with a fake transport (the real run needs the network)."""

import importlib.util
import json
from pathlib import Path

import pytest

from app.core.simbad_settings import SimbadSettings
from app.services.simbad import SimbadUnavailable
from app.services.simbad_precomputed import load_precomputed
from tests.test_catalog_objects import j2000_row
from tests.test_simbad_service import FakeTransport, tap

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_simbad_golden.py"
spec = importlib.util.spec_from_file_location("generate_simbad_golden", SCRIPT)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
CFG = SimbadSettings(tap_url="https://tap.example/sync")


def test_plan_and_adql_for_barnard():
    plan = gen.target_plan("BARNARDS_STAR")
    assert plan["radius"] == pytest.approx(0.059, abs=1e-3) and plan["years"] == 26.75  # last visit: 2026-08-21
    t = FakeTransport(tap(j2000_row("BARNARDS_STAR")))
    data, warnings = gen.generate("BARNARDS_STAR", CFG, transport=t)
    assert "DISTANCE(" in t.calls[0][1] and data["query"]["type_filter"] == "all"
    assert any("OK" in w and "px from the image centre" in w for w in warnings)
    assert data["objects"][0]["pmdec"] == pytest.approx(10362.394)


def test_a_target_missing_from_the_answer_is_warned_about():
    far = ["Other", "*", "Star", 10.0, 10.0, None, None, 1, None, None, None, None, None, None]
    _, warnings = gen.generate("BARNARDS_STAR", CFG, transport=FakeTransport(tap(far)))
    assert any("no SIMBAD object lands within" in w for w in warnings)


def test_main_writes_files_the_api_can_load_and_never_half_writes(tmp_path, capsys):
    ok = FakeTransport(tap(j2000_row("BARNARDS_STAR")))
    assert gen.main(["--target", "BARNARDS_STAR", "--out-dir", str(tmp_path)], transport=ok) == 0
    rows = load_precomputed("BARNARDS_STAR", tmp_path)
    assert rows and rows[0].main_id == "Barnard's Star"
    assert json.loads((tmp_path / "simbad" / "barnards_star.json").read_text())["tap_url"]

    bad = FakeTransport(error=SimbadUnavailable("down"))
    assert gen.main(["--target", "WISE_0855", "--out-dir", str(tmp_path)], transport=bad) == 1
    assert not (tmp_path / "simbad" / "wise_0855.json").exists()
    assert "FAILED" in capsys.readouterr().err


def test_dry_run_prints_all_five_queries_without_network(capsys):
    assert gen.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    for key in ("BARNARDS_STAR", "EPSILON_INDI_B", "WISE_0855", "RW_AURIGAE", "R_DRACONIS"):
        assert f"-- {key}" in out
    assert out.count("CONTAINS(POINT('ICRS', b.ra, b.dec)") >= 10


def test_check_otypes_reports_unknown_codes():
    known = {"data": [["*"], ["**"], ["G"], ["QSO"]]}
    result = gen.check_otypes(CFG, transport=lambda *a: known)
    assert "MS*" in result["no_common_stars"] and "*" not in result["no_common_stars"]
    assert "AGN" in result["ir_galaxies"] and "G" not in result["ir_galaxies"]
