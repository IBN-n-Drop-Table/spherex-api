"""Range-request test on the public SPHEREx S3 bucket (nasa-irsa-spherex), no IRSA involved.

Reads only the FITS headers and a small cutout, by HTTP Range requests, from:
  * a DR1 HiPS cube tile (dr1/hips5_cube/..., 512x512x102 float32, HEALPix order 5), and
  * a DR1 Level 2 frame (dr1/level2/..., IMAGE extension 2040x2040 float32).
Measured 2026-11-15: HiPS 40x40 px x 17 channels = 1.4 MB in 3.0 s (+1.1 s headers);
L2 frame 60x60 px = 0.49 MB in 0.40 s (+0.34 s headers).

Usage: python scripts/s3_range_test.py
"""
import io, time, requests, numpy as np
from astropy.io import fits
B = "https://nasa-irsa-spherex.s3.amazonaws.com/"
S = requests.Session()
def rng(url, a, b):
    r = S.get(url, headers={"Range": f"bytes={a}-{b}"}, timeout=60); r.raise_for_status(); return r.content
def headers(url, n_ext):
    """Read headers by ranges; return [(header, data_offset)] for the first n_ext HDUs."""
    out, pos, buf = [], 0, b""
    for _ in range(n_ext):
        while True:
            if len(buf) < pos + 2880 * 40:
                buf += rng(url, len(buf), len(buf) + 2880 * 40 - 1)
            end = buf.find(b"END" + b" " * 77, pos)
            if end >= 0 and (end - pos) % 80 == 0:
                break
            if end >= 0:
                # not card-aligned: search on
                k = end
                while k >= 0 and (k - pos) % 80: k = buf.find(b"END" + b" " * 77, k + 1)
                if k >= 0: end = k; break
        hlen = ((end + 80 - pos + 2879) // 2880) * 2880
        h = fits.Header.fromstring(buf[pos:pos + hlen].decode("ascii"))
        dstart = pos + hlen
        naxes = [h.get(f"NAXIS{i}", 0) for i in range(1, h.get("NAXIS", 0) + 1)]
        nbytes = abs(h.get("BITPIX", 8)) // 8 * (int(np.prod(naxes)) if naxes else 0) + h.get("PCOUNT", 0) * 0
        out.append((h, dstart))
        pos = dstart + ((nbytes + 2879) // 2880) * 2880
    return out

# HiPS tile: 512x512x102 float32 cube in HDU 1; a 40x40 px cutout (~8.6') from 17 of the 102 channels.
t = time.monotonic()
tile = B + "dr1/hips5_cube/hips_20261026/Norder5/Dir0/Npix1000_cube.fits"
(h0, _), (h1, d1) = headers(tile, 2)
th = time.monotonic() - t
nx, ny = h1["NAXIS1"], h1["NAXIS2"]
y0, ny_cut, x0, nx_cut = 236, 40, 236, 40
chans = list(range(0, 102, 6))
t = time.monotonic(); planes = []; nbytes = 0
for c in chans:
    a = d1 + 4 * (c * nx * ny + y0 * nx)
    raw = rng(tile, a, a + 4 * ny_cut * nx - 1); nbytes += len(raw)
    planes.append(np.frombuffer(raw, ">f4").reshape(ny_cut, nx)[:, x0:x0 + nx_cut])
tc = time.monotonic() - t
bb = np.nanmean(planes, axis=0)
print(f"HiPS tile: headers {th:.2f}s; cutout {nx_cut}x{ny_cut}px x {len(chans)} channels: {len(chans)} range requests, "
      f"{nbytes/1e6:.2f} MB, {tc:.2f}s; finite px {np.isfinite(bb).mean():.0%}, median {np.nanmedian(bb):.3g} {h1.get('BUNIT')}")

# L2 frame: IMAGE extension (HDU 1); a 60x60 native px (~6') cutout of IMAGE only.
t = time.monotonic()
frame = B + "dr1/level2/2025W25_1B/l2b-v27-2026-213/1/level2_2025W25_1B_0001_1D1_spx_l2b-v27-2026-213.fits"
hd = headers(frame, 2)
th = time.monotonic() - t
h, d = hd[1]
print("frame HDU1:", h.get("EXTNAME"), h.get("XTENSION"), h.get("BITPIX"), h.get("NAXIS1"), h.get("NAXIS2"), "ZIMAGE" in h)
nx = h["NAXIS1"]; bp = abs(h["BITPIX"]) // 8
t = time.monotonic()
raw = rng(frame, d + bp * 1000 * nx, d + bp * (1000 + 60) * nx - 1)
tc = time.monotonic() - t
cut = np.frombuffer(raw, ">f4" if h["BITPIX"] == -32 else ">f8").reshape(60, nx)[:, 1000:1060]
print(f"L2 frame: headers {th:.2f}s; 60x60 cutout of IMAGE (rows block): 1 request, {len(raw)/1e6:.2f} MB, {tc:.2f}s; "
      f"finite {np.isfinite(cut).mean():.0%}")
