"""
Objetos de prueba del reto. ¡OJO con el movimiento propio!
Barnard y WISE 0855 se mueven ~10"/año y ~8"/año: sus coordenadas J2000 están
a varios minutos de arco de donde los ve SPHEREx en 2025-2026. Por eso se
propagan a la época media de SPHEREx antes de pedir el parche.

Valores aproximados (comprobar en SIMBAD). Si hay red a SIMBAD, precompute.py
los refresca automáticamente.
"""
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

SPHEREX_MID = Time("2026-01-01")

# name, ra, dec (deg), pmra*cos(dec), pmdec (mas/yr), época de la posición, fov (deg), simbad_id
_RAW = [
    ("Barnard's Star", 269.44850, 4.73942, -801.55, 10362.39, 2016.0, 0.1, "Barnard's star"),
    ("Epsilon Indi B", 331.04383, -56.78269, 3966.7, -2536.2, 2000.0, 0.1, "eps Ind B"),
    ("WISE 0855-0714", 133.795129, -7.245147, -8118.0, 680.0, 2000.0, 0.15, "WISE J085510.83-071442.5"),
    ("RW Aurigae", 76.95645, 30.40142, 0.0, 0.0, 2000.0, 0.1, "RW Aur"),
    ("R Draconis", 248.16750, 66.75500, 0.0, 0.0, 2000.0, 0.1, "R Dra"),
]


def propagate(ra, dec, pmra, pmdec, epoch, to=SPHEREX_MID):
    c = SkyCoord(ra * u.deg, dec * u.deg, pm_ra_cosdec=pmra * u.mas / u.yr,
                 pm_dec=pmdec * u.mas / u.yr, obstime=Time(epoch, format="jyear"))
    c2 = c.apply_space_motion(new_obstime=to)
    return float(c2.ra.deg), float(c2.dec.deg)


def build_targets(raw=_RAW):
    out = []
    for name, ra, dec, pmra, pmdec, ep, fov, sid in raw:
        ra2, dec2 = propagate(ra, dec, pmra, pmdec, ep)
        out.append({"name": name, "simbad_id": sid, "ra_deg": round(ra2, 6), "dec_deg": round(dec2, 6),
                    "fov_deg": fov, "pm_mas_yr": [pmra, pmdec],
                    "note": "posición propagada a 2026.0 por movimiento propio"})
    return out


TARGETS = build_targets()
