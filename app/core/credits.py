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
                    "and R Draconis, propagated to each SPHEREx exposure date; in the spot-the-mover game, "
                    "the candidate stars and the correct answer of every round",
        "citation": "Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1",
        "doi": None,
        "url": "https://gea.esac.esa.int/archive/",
        "access": "https://gaia.ari.uni-heidelberg.de/tap",
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

SOFTWARE = ["FastAPI", "Astropy", "Astroquery/PyVO", "reproject", "astropy-healpix", "NumPy", "SciPy", "OpenCV",
            "Matplotlib", "Pillow"]

# SIMBAD/CDS: used only by /catalog-objects, so it is kept out of DATA_CREDITS (what /sky-patch and /spectrum
# cite) and listed by /credits through ALL_DATA_CREDITS. Citation checked on 2026-11-15 against SIMBAD's own page
# (Harvard mirror: "2000,A&AS,143,9", "The SIMBAD astronomical database", Wenger et al.) and the DOI against
# Crossref (10.1051/aas:2000332 -> A&AS 143(1), 9-22, 2000). The README's "Data used" section carries the same wording.
SIMBAD_CREDIT: dict = {
    "id": "simbad",
    "source": "SIMBAD astronomical database, CDS, Strasbourg Astronomical Observatory, France",
    "dataset": "SIMBAD",
    "used_for": "Catalogued objects (identifier, type, position, proper motion, magnitude, bibliographic "
                "references) marked on the SPHEREx images, with positions propagated to the observation date; "
                "the star names of the spot-the-mover game's WISE vs SPHEREx rounds",
    "citation": "Wenger et al. 2000, A&AS 143, 9",
    "doi": "10.1051/aas:2000332",
    "url": "https://simbad.cds.unistra.fr/simbad/",
    "access": "https://simbad.cds.unistra.fr/simbad/sim-tap",
}

SIMBAD_ACKNOWLEDGEMENT = "This research has made use of the SIMBAD database, operated at CDS, Strasbourg, France"

# Data used only by the spot-the-mover game's WISE vs SPHEREx round pool (scripts/build_game_pool.py),
# so kept out of DATA_CREDITS too. The HiPS cube is part of SPHEREx DR1, whose release DOI it carries.
# unWISE, as checked on https://unwise.me/ on 2026-11-15: the site asks users to cite Lang 2014 (AJ 147,
# 108) and states no licence for the coadd data. Its "CC BY-NC-SA 4.0 / Required attribution" lines
# caption only the three showcase images of its home page, so no licence is claimed here; their
# attribution line is used as the source name, as a courtesy.
GAME_DATA_CREDITS: list[dict] = [
    {
        "id": "spherex_dr1_hips",
        "source": "SPHEREx (NASA/JPL/Caltech), served by IRSA/IPAC",
        "dataset": "SPHEREx Data Release 1 HiPS spectral cube (hips5_cube), AWS Open Data bucket nasa-irsa-spherex",
        "used_for": "SPHEREx images of the spot-the-mover game's WISE vs SPHEREx rounds: the 1.1-2.4 um "
                    "channels of the DR1 HiPS cube, read by HTTP Range requests",
        "citation": None,
        "doi": "10.26131/IRSA678",
        "url": "https://irsa.ipac.caltech.edu/cloud_access/",
        "access": "https://nasa-irsa-spherex.s3.amazonaws.com/dr1/hips5_cube/",
    },
    {
        "id": "unwise",
        "source": "unWISE / NASA/JPL-Caltech / D. Lang (Perimeter Institute)",
        "dataset": "unWISE coadds of the WISE imaging, AllWISE version (WISE mission 2010-2011)",
        "used_for": "WISE (2010) images of the spot-the-mover game's WISE vs SPHEREx rounds: W1 (3.4 um) cutouts",
        "citation": "Lang 2014, AJ 147, 108",
        "doi": None,
        "url": "https://unwise.me/",
        "access": "https://unwise.me/cutout_fits",
    },
]

# Everything this API uses: what GET /credits lists.
ALL_DATA_CREDITS: list[dict] = DATA_CREDITS + [SIMBAD_CREDIT] + GAME_DATA_CREDITS
