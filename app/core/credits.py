"""Data citations: one source of truth for /credits and `data_credits`.

The same citations as the README's "Data used" section, word for word; a test
(tests/test_credits.py) checks that every DOI, URL and citation here also
appears there. The challenge requires citing the NASA data used.
"""

from __future__ import annotations

DATA_CREDITS: list[dict] = [
    {
        "id": "spherex_dr1",
        "source": "SPHEREx (NASA/JPL/Caltech), served by IRSA/IPAC",
        "dataset": "SPHEREx Data Release 1, collection spherex_dr1",
        "used_for": "All images, difference images, spectra and light curves: Level 2 Spectral Images "
                    "(MEF), downloaded as server-side cutouts",
        "citation": None,
        "doi": "10.26131/IRSA678",
        "url": "https://www.ipac.caltech.edu/doi/10.26131/IRSA678",
        "access": "https://irsa.ipac.caltech.edu/SIA",
    },
    {
        "id": "spherex_qr3",
        "source": "SPHEREx (NASA/JPL/Caltech), served by IRSA/IPAC",
        "dataset": "SPHEREx Quick Release 3, collection spherex_qr3",
        "used_for": "All images, difference images, spectra and light curves: Level 2 Spectral Images "
                    "(MEF), downloaded as server-side cutouts",
        "citation": None,
        "doi": "10.26131/IRSA662",
        "url": "https://www.ipac.caltech.edu/doi/10.26131/IRSA662",
        "access": "https://irsa.ipac.caltech.edu/SIA",
    },
    {
        "id": "gaia_dr3",
        "source": "Gaia DR3 (ESA)",
        "dataset": "Gaia Data Release 3",
        "used_for": "Reference positions and proper motions of Barnard's Star, Epsilon Indi B, RW Aurigae "
                    "and R Draconis, propagated to each SPHEREx exposure date",
        "citation": "Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1",
        "doi": None,
        "url": "https://gea.esac.esa.int/archive/",
        "access": None,
    },
    {
        "id": "wise",
        "source": "WISE (NASA)",
        "dataset": "WISE",
        "used_for": "Reference position of WISE 0855-0714 (from its WISE designation, epoch ~2010.4); "
                    "proper motion from Luhman & Esplin (2016, AJ 152, 78)",
        "citation": "Luhman & Esplin (2016, AJ 152, 78)",
        "doi": None,
        "url": "https://irsa.ipac.caltech.edu/Missions/wise.html",
        "access": None,
    },
]

SPHEREX_ACKNOWLEDGEMENT = (
    "This publication makes use of data products from the Spectro-Photometer for the History of the "
    "Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet "
    "Propulsion Laboratory and the California Institute of Technology, and is funded by the National "
    "Aeronautics and Space Administration."
)
ACKNOWLEDGEMENT_SOURCE = "https://irsa.ipac.caltech.edu/Missions/spherex.html"

SOFTWARE = ["FastAPI", "Astropy", "Astroquery/PyVO", "reproject", "NumPy", "OpenCV", "Matplotlib", "Pillow"]

# SIMBAD/CDS: served by /catalog-objects only (kept out of DATA_CREDITS so that /credits and the other
# responses stay unchanged). The README's "Data used" section carries the same wording (tests/test_catalog_objects.py).
SIMBAD_CREDIT: dict = {
    "id": "simbad",
    "source": "SIMBAD astronomical database, CDS, Strasbourg Astronomical Observatory, France",
    "dataset": "SIMBAD",
    "used_for": "Catalogued objects (identifier, type, position, proper motion, magnitude, bibliographic "
                "references) marked on the SPHEREx images, with positions propagated to the observation date",
    "citation": "Wenger et al. 2000, A&AS 143, 9",
    "doi": "10.1051/aas:2000332",
    "url": "https://simbad.cds.unistra.fr/simbad/",
    "access": "https://simbad.cds.unistra.fr/simbad/sim-tap",
}
