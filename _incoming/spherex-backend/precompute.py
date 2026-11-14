"""
"Golden path": pre-calcula los objetos de prueba para que la demo responda en ms.
Ejecútalo antes de la presentación (y cada vez que IRSA publique datos nuevos):

    python precompute.py            # usa caché de recortes; solo baja lo nuevo
    python precompute.py --force    # recalcula los coadds/PNG aunque exista result.json
"""
import json
import os
import sys
import time

import spherex_core as core
from targets import _RAW, build_targets


def refresh_from_simbad():
    """Intenta actualizar posiciones/movimientos propios desde SIMBAD."""
    try:
        from astroquery.simbad import Simbad
        s = Simbad(); s.add_votable_fields("pmra", "pmdec")
        raw = []
        for name, ra, dec, pmra, pmdec, ep, fov, sid in _RAW:
            t = s.query_object(sid)
            if t is None or len(t) == 0:
                raw.append((name, ra, dec, pmra, pmdec, ep, fov, sid)); continue
            col = lambda *n: next(t[c][0] for c in n if c in t.colnames)
            pr, pd = col("pmra", "PMRA"), col("pmdec", "PMDEC")
            raw.append((name, float(col("ra", "RA_d")), float(col("dec", "DEC_d")),
                        float(pr) if pr is not None else 0.0, float(pd) if pd is not None else 0.0,
                        2000.0, fov, sid))
        print("Posiciones actualizadas desde SIMBAD")
        return build_targets(raw)
    except Exception as e:
        print("SIMBAD no disponible, uso valores locales:", e)
        return build_targets()


def main():
    force = "--force" in sys.argv
    targets = refresh_from_simbad()
    for t in targets:
        t0 = time.time()
        print(f"\n=== {t['name']}  ra={t['ra_deg']} dec={t['dec_deg']} fov={t['fov_deg']}°")
        res = core.build_sky_patch(t["ra_deg"], t["dec_deg"], t["fov_deg"], max_per_det=0,
                                   name=t["name"], force=force)
        t["sky_patch_query"] = f"/api/v1/sky-patch?ra={t['ra_deg']}&dec={t['dec_deg']}&fov={t['fov_deg']}&max_per_det=0"
        t["epochs"] = [e["epoch_id"] for e in res["timelapse_epochs"]]
        print(f"   {len(t['epochs'])} épocas, {res['target_info']['n_images_used']} imágenes, "
              f"{len(res['difference_imaging']['detected_anomalies'])} candidatos, {time.time()-t0:.0f}s")
    with open(os.path.join(core.STATIC_DIR, "targets.json"), "w") as f:
        json.dump(targets, f, indent=1)


if __name__ == "__main__":
    main()
