"""RA/Dec parsing for query strings: decimal degrees or sexagesimal.

Pure Python on purpose (the API must not need astropy).

Accepted RA:  "133.7951", "133.7951d", "08h55m10.831s", "08:55:10.831", "8 55 10.831"
              (sexagesimal RA is always hours, as in astronomy)
Accepted Dec: "-7.2451", "-7.2451d", "-7d14m42.53s", "-07:14:42.53", "-7 14 42.53",
              also with the unicode marks ° ′ ″ and ' "
"""

from __future__ import annotations

import math
import re

_NUM = r"(\d+(?:\.\d*)?|\.\d+)"
_SEXA = re.compile(
    rf"^\s*([+-])?\s*{_NUM}\s*(?:[hd°:\s])\s*{_NUM}\s*(?:[m′':\s])\s*{_NUM}?\s*(?:[s″\"])?\s*$",
    re.IGNORECASE,
)
_SEXA_HM = re.compile(rf"^\s*([+-])?\s*{_NUM}\s*[hd°:\s]\s*{_NUM}\s*[m′']?\s*$", re.IGNORECASE)
_DEG = re.compile(rf"^\s*([+-]?)\s*{_NUM}\s*(?:d|deg|°)?\s*$", re.IGNORECASE)


class CoordinateError(ValueError):
    pass


def _sexagesimal(text: str) -> tuple[float, bool] | None:
    """(value in units of the first field, has_hour_marker) or None."""
    m = _SEXA.match(text)
    if m:
        sign, a, b, c = m.groups()
        value = float(a) + float(b) / 60 + float(c or 0) / 3600
    else:
        m = _SEXA_HM.match(text)
        if not m:
            return None
        sign, a, b = m.groups()
        value = float(a) + float(b) / 60
    if float(b) >= 60 or (m.re is _SEXA and float(m.group(4) or 0) >= 60):
        raise CoordinateError(f"minutes/seconds must be < 60 in {text!r}")
    return (-value if sign == "-" else value), "h" in text.lower()


def parse_ra(value: str | float) -> float:
    """Right ascension -> degrees in [0, 360)."""
    if isinstance(value, (int, float)):
        deg = float(value)
    else:
        text = value.strip()
        m = _DEG.match(text)
        if m:
            deg = float(m.group(1) + m.group(2))
        else:
            sexa = _sexagesimal(text)
            if sexa is None:
                raise CoordinateError(f"cannot parse RA {value!r}: use degrees or 08h55m10.8s")
            hours, _ = sexa
            if not 0 <= hours < 24:
                raise CoordinateError(f"RA hours must be in [0, 24): {value!r}")
            deg = hours * 15.0
    if not (math.isfinite(deg) and 0 <= deg < 360):
        raise CoordinateError(f"RA must be in [0, 360) degrees: {value!r}")
    return deg


def parse_dec(value: str | float) -> float:
    """Declination -> degrees in [-90, 90]."""
    if isinstance(value, (int, float)):
        deg = float(value)
    else:
        text = value.strip()
        m = _DEG.match(text)
        if m:
            deg = float(m.group(1) + m.group(2))
        else:
            sexa = _sexagesimal(text)
            if sexa is None:
                raise CoordinateError(f"cannot parse Dec {value!r}: use degrees or -7d14m42.5s")
            deg, has_hours = sexa
            if has_hours:
                raise CoordinateError(f"Dec cannot be in hours: {value!r}")
    if not (math.isfinite(deg) and -90 <= deg <= 90):
        raise CoordinateError(f"Dec must be in [-90, 90] degrees: {value!r}")
    return deg
