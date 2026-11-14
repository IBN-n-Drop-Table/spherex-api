"""Pure helpers: RA/Dec parsing and relative/absolute image URLs."""

import pytest

from app.core.coords import CoordinateError, parse_dec, parse_ra
from app.core.urls import INTERNAL_BASE, normalize_base, to_internal, to_public, to_relative


@pytest.mark.parametrize("text, deg", [
    ("133.7951", 133.7951), ("133.7951d", 133.7951), (133.7951, 133.7951),
    ("08h55m10.831s", 133.795129), ("08:55:10.831", 133.795129), ("8 55 10.831", 133.795129),
    ("17h57m48.5s", 269.452083),
])
def test_parse_ra(text, deg):
    assert parse_ra(text) == pytest.approx(deg, abs=1e-6)


@pytest.mark.parametrize("text, deg", [
    ("-7.2451", -7.2451), ("-7d14m42.53s", -7.245147), ("-07:14:42.53", -7.245147),
    ("-7 14 42.53", -7.245147), ("+04°41′36″", 4.693333), ("-0d30m0s", -0.5), ("-00:30:00", -0.5),
])
def test_parse_dec(text, deg):
    assert parse_dec(text) == pytest.approx(deg, abs=1e-6)


@pytest.mark.parametrize("fn, text", [
    (parse_ra, "25h00m00s"), (parse_ra, "abc"), (parse_ra, "360"), (parse_ra, "10:70:00"),
    (parse_dec, "-91"), (parse_dec, "8h1m1s"),
])
def test_bad_coordinates(fn, text):
    with pytest.raises(CoordinateError):
        fn(text)


def test_url_round_trip():
    disk = {"image_url": "/static/golden_path/a.png", "other": "https://cdn.example.org/x.png",
            "nested": [{"u": "/static/live/1/b.png"}], "n": 3}
    internal = to_internal(disk)
    assert internal["image_url"] == INTERNAL_BASE + "/static/golden_path/a.png"
    assert internal["other"] == disk["other"]  # foreign URLs untouched
    assert to_relative(internal) == disk
    public = to_public(internal, "https://api.example.org/")
    assert public["image_url"] == "https://api.example.org/static/golden_path/a.png"
    assert public["nested"][0]["u"] == "https://api.example.org/static/live/1/b.png"


@pytest.mark.parametrize("base", ["https://x.org", "https://x.org/", "https://x.org/static", "https://x.org/static/"])
def test_public_base_forms(base):
    assert normalize_base(base) == "https://x.org"
