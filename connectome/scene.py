"""3D scene export: one self-contained HTML file for viewing the connectome.

Coordinates are RAS millimetres. Three sources are placed in one frame:

* brain regions, brain streamlines and the brain outline stay in the
  diffusion scan's own space (ds000114 sub-01);
* the PAM50 cord (grey-matter zones, tract centrelines, outline) is
  translated so the top of C1 meets the lowest slice of the brainstem label;
* the cervical cord tractography (a different subject) is translated onto
  PAM50 C3-C5. Only translations are applied; nothing is rescaled.

Brainstem nuclei, cerebellum, cranial nerves and peripheral input/output
pools have no voxel label in these data, so they get schematic positions
relative to the structures above. Every node records which kind it is.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy import ndimage

from .graph import BRAINSTEM_NUCLEI, TRACTS
from .sources import SPINAL_LEVELS

VIEWER_TEMPLATE = Path(__file__).with_name("viewer3d.html")
PLACEHOLDER = "/*__SCENE__*/null"
QUANT = 10.0  # int16 coordinates in 0.1 mm

# Relative height inside the brainstem label (0 = lowest slice, 1 = top) and
# anterior(+)/posterior(-) offset in mm. Schematic, from standard anatomy.
NUCLEUS_LAYOUT = {
    "Superior colliculus": (0.95, -8.0, 4.0),
    "Red nucleus": (0.85, 2.0, 4.0),
    "Reticular formation": (0.50, -2.0, 4.0),
    "Vestibular nuclei": (0.35, -6.0, 8.0),
    "Inferior olive": (0.12, 6.0, 5.0),
    "Cuneate nucleus": (0.04, -8.0, 5.0),
    "Gracile nucleus": (0.02, -8.0, 2.5),
}
GM_ZONES = {30: ("ventral horn", "L"), 31: ("ventral horn", "R"),
            32: ("intermediate zone", "L"), 33: ("intermediate zone", "R"),
            34: ("dorsal horn", "L"), 35: ("dorsal horn", "R")}


def voxel_to_world(ijk: np.ndarray, affine: np.ndarray) -> np.ndarray:
    return ijk @ affine[:3, :3].T + affine[:3, 3]


def surface_points(mask: np.ndarray, affine: np.ndarray, n: int, rng, step: int = 1) -> np.ndarray:
    """Random sample of the mask's boundary voxels, in world mm."""
    m = mask[::step, ::step, ::step].astype(bool)
    edge = m & ~ndimage.binary_erosion(m)
    ijk = np.argwhere(edge).astype(float) * step
    if len(ijk) > n:
        ijk = ijk[rng.choice(len(ijk), n, replace=False)]
    return voxel_to_world(ijk, affine)


def decimate(streamlines, n: int, rng, spacing_mm: float = 3.0) -> list[np.ndarray]:
    """Random subset of streamlines, resampled to roughly ``spacing_mm``."""
    sl = list(streamlines)
    if len(sl) > n:
        sl = [sl[i] for i in sorted(rng.choice(len(sl), n, replace=False))]
    out = []
    for s in sl:
        s = np.asarray(s, float)
        if len(s) < 2:
            continue
        seg = np.linalg.norm(np.diff(s, axis=0), axis=1)
        arc = np.concatenate([[0], np.cumsum(seg)])
        k = max(2, int(arc[-1] // spacing_mm) + 1)
        t = np.linspace(0, arc[-1], k)
        out.append(np.stack([np.interp(t, arc, s[:, d]) for d in range(3)], -1))
    return out


def _b64(a: np.ndarray, dtype) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype=np.dtype(dtype).newbyteorder("<")).tobytes()).decode()


def pack_points(pts: np.ndarray, offset=(0.0, 0.0, 0.0)) -> str:
    """Base64 little-endian int16 xyz triples in 0.1 mm."""
    q = np.round((np.asarray(pts, float).reshape(-1, 3) + np.asarray(offset)) * QUANT)
    if np.abs(q).max(initial=0) > np.iinfo(np.int16).max:
        raise ValueError("coordinate outside int16 range at 0.1 mm")
    return _b64(q.ravel(), np.int16)


def unpack_points(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype="<i2").reshape(-1, 3) / QUANT


def pack_lines(lines: list[np.ndarray], offset=(0.0, 0.0, 0.0)) -> dict:
    """Concatenated points (see ``pack_points``) + base64 uint16 point counts."""
    if not lines:
        return {"xyz": "", "len": ""}
    return {"xyz": pack_points(np.concatenate(lines), offset),
            "len": _b64(np.array([len(s) for s in lines]), np.uint16)}


def label_centroids(label_img: np.ndarray, affine: np.ndarray) -> dict[int, np.ndarray]:
    idx = [int(v) for v in np.unique(label_img) if v]
    com = ndimage.center_of_mass(np.ones_like(label_img, float), label_img, idx)
    return {i: voxel_to_world(np.asarray(c)[None], affine)[0] for i, c in zip(idx, com)}


def pam50_geometry(data_dir: Path) -> dict:
    """Per-level centroids (world mm) of every PAM50 atlas label, the cord
    outline mask and the C1 top point."""
    d = Path(data_dir) / "pam50"
    lv_img = nib.load(d / "PAM50_spinal_levels.nii.gz")
    levels = np.asarray(lv_img.dataobj).astype(int)
    aff = lv_img.affine
    z_of = [np.unique(np.nonzero(levels == k)[2]) for k in range(1, len(SPINAL_LEVELS) + 1)]
    ii, jj = np.meshgrid(np.arange(levels.shape[0]), np.arange(levels.shape[1]), indexing="ij")
    cent = {}
    for idx in range(36):
        p = np.asarray(nib.load(d / f"PAM50_atlas_{idx:02d}.nii.gz").dataobj, np.float32)
        w = p.sum((0, 1))
        wi = (p * ii[..., None]).sum((0, 1))
        wj = (p * jj[..., None]).sum((0, 1))
        rows = []
        for z in z_of:
            tot = w[z].sum() if len(z) else 0.0
            if tot <= 0:
                rows.append(None)
                continue
            vox = np.array([wi[z].sum() / tot, wj[z].sum() / tot, float(np.average(z, weights=w[z]))])
            rows.append(voxel_to_world(vox[None], aff)[0])
        cent[idx] = rows
    cord = [voxel_to_world(np.argwhere(levels == k).mean(0)[None], aff)[0]
            for k in range(1, len(SPINAL_LEVELS) + 1)]
    top = z_of[0].max()
    top_xy = np.argwhere(levels[..., top] > 0).mean(0)
    c1_top = voxel_to_world(np.array([[top_xy[0], top_xy[1], top]]), aff)[0]
    return {"centroids": cent, "cord_centroids": cord, "mask": levels > 0, "affine": aff,
            "c1_top": c1_top}


def brainstem_frame(label_img, affine, brainstem_index):
    ijk = np.argwhere(label_img == brainstem_index)
    xyz = voxel_to_world(ijk.astype(float), affine)
    zlo, zhi = xyz[:, 2].min(), xyz[:, 2].max()
    low = xyz[xyz[:, 2] <= zlo + 2.5]
    return {"bottom": np.array([*low[:, :2].mean(0), zlo]), "zlo": zlo, "zhi": zhi,
            "center": xyz.mean(0)}


def build_scene(data_dir, G, br, sp=None, *, n_brain_streamlines=4000,
                n_cord_streamlines=1500, seed=0) -> dict:
    rng = np.random.default_rng(seed)
    data_dir = Path(data_dir)
    table = br.labels
    cen = label_centroids(br.label_img, br.affine)
    pos, placement = {}, {}
    for r in table:
        if r["index"] in cen:
            pos[r["id"]] = cen[r["index"]]
            placement[r["id"]] = "atlas centroid (subject space)"

    bs_index = next(r["index"] for r in table if r["name"] == "Brain-Stem")
    bs = brainstem_frame(br.label_img, br.affine, bs_index)
    h = bs["zhi"] - bs["zlo"]
    for side, sx in (("L", -1), ("R", 1)):  # RAS: +x is the subject's right
        for name in BRAINSTEM_NUCLEI:
            fz, ay, lx = NUCLEUS_LAYOUT[name]
            pos[f"{name} ({side})"] = np.array([bs["center"][0] + sx * lx,
                                                bs["center"][1] + ay, bs["zlo"] + fz * h])
            placement[f"{name} ({side})"] = "schematic (inside brainstem label)"
        pos[f"Cerebellum ({side})"] = np.array([bs["center"][0] + sx * 25,
                                                bs["center"][1] - 40, bs["zlo"] + 0.3 * h])
        placement[f"Cerebellum ({side})"] = "schematic (no cerebellum label in atlas)"

    # PAM50 cord, translated so C1 top meets the brainstem's lowest slice
    pam = pam50_geometry(data_dir)
    shift = bs["bottom"] - pam["c1_top"]

    def cord(p):
        return None if p is None else p + shift

    for idx, (zone, side) in GM_ZONES.items():
        last_offset = None  # zone position relative to the cord centre
        for li, level in enumerate(SPINAL_LEVELS):
            nid = f"{level} {side} {zone}"
            p = pam["centroids"][idx][li]
            c = pam["cord_centroids"][li]
            if p is not None:
                pos[nid], last_offset = cord(p), p - c
                placement[nid] = "PAM50 atlas centroid"
            elif last_offset is not None:  # atlas grey matter ends above S3
                pos[nid] = cord(c + last_offset)
                placement[nid] = "extrapolated (PAM50 grey-matter atlas ends at S2)"

    # Peripheral pools: offsets from the grey-matter zone they attach to
    # (x lateral, y anterior). Schematic.
    periph = {"DRG": ("dorsal horn", 14.0, -6.0), "motor output": ("ventral horn", 16.0, 8.0),
              "sympathetic output": ("intermediate zone", 22.0, 14.0),
              "parasympathetic output": ("intermediate zone", 20.0, 16.0)}
    for kind, (zone, lat, ant) in periph.items():
        for level in SPINAL_LEVELS:
            for side in "LR":
                nid = f"{level} {side} {kind}"
                base = pos.get(f"{level} {side} {zone}")
                if nid in G and base is not None:
                    pos[nid] = base + np.array([lat if side == "R" else -lat, ant, 0.0])
                    placement[nid] = "schematic (outside the cord)"

    # Cranial inputs/outputs: a row in front of the brainstem
    cranial = [n for n, d in G.nodes(data=True) if d.get("kind") == "cranial nerve"]
    for k, n in enumerate(sorted(cranial)):
        role = G.nodes[n].get("role")
        x = bs["center"][0] + (k - len(cranial) / 2) * 12
        y = bs["center"][1] + (60 if role == "input" else 45)
        z = bs["zlo"] + (0.9 if role == "input" else 0.4) * h
        pos[n] = np.array([x, y, z])
        placement[n] = "schematic (cranial nerve)"

    missing = [n for n in G if n not in pos]
    if missing:
        raise ValueError(f"no 3D position for {len(missing)} nodes, e.g. {missing[:3]}")

    # Tract centrelines per (tract, side), for routing PAM50 edges
    tracts = []
    for t in TRACTS:
        for side, idx in zip("LR", t["ids"]):
            pts = [(li, cord(p)) for li, p in enumerate(pam["centroids"][idx]) if p is not None]
            tracts.append({"name": t["name"], "side": side, "dir": t["dir"], "atlas_id": idx,
                           "levels": [li for li, _ in pts],
                           "xyz": pack_points(np.array([p for _, p in pts]))})

    node_ids = list(G.nodes)
    index = {n: i for i, n in enumerate(node_ids)}
    pathways, pw_index, edges = [], {}, []
    prov_code = {"dti_brain": 0, "pam50_tract": 1, "anatomy": 2}
    for u, v, d in G.edges(data=True):
        if d["provenance"] == "dti_brain" and u > v:
            continue
        pw = d["pathway"]
        if pw not in pw_index:
            pw_index[pw] = len(pathways)
            pathways.append(pw)
        edges.append([index[u], index[v], prov_code[d["provenance"]], round(float(d["weight"]), 2),
                      pw_index[pw], int(d.get("atlas_id", -1))])

    nodes = []
    for n in node_ids:
        a = G.nodes[n]
        nodes.append({"id": n, "role": a.get("role", ""), "kind": a.get("kind", ""),
                      "system": a.get("system", ""), "cell": a.get("cell_type", ""),
                      "level": a.get("level_index", -1), "placement": placement[n]})

    brain_sl = decimate(br.streamlines, n_brain_streamlines, rng)
    brain_mask = br.mask if br.mask is not None else br.fa > 0
    scene = {
        "units": "RAS mm (int16 at 0.1 mm)",
        "nodes": nodes,
        "pos": pack_points(np.array([pos[n] for n in node_ids])),
        "edges": edges,
        "pathways": pathways,
        "tracts": tracts,
        "levels": SPINAL_LEVELS,
        "brainstem": {"center": pack_points(bs["center"][None]), "bottom": pack_points(bs["bottom"][None])},
        "brain_outline": pack_points(surface_points(brain_mask, br.affine, 14000, rng)),
        "cord_outline": pack_points(surface_points(pam["mask"], pam["affine"], 14000, rng, step=3), shift),
        "brain_streamlines": pack_lines(brain_sl),
        "stats": {"brain_streamlines_total": int(br.n_streamlines),
                  "brain_streamlines_shown": len(brain_sl)},
    }
    if sp is not None and sp.streamlines is not None and len(sp.streamlines):
        cord_sl = decimate(sp.streamlines, n_cord_streamlines, rng, spacing_mm=2.0)
        mids = [pam["centroids"][32][li] for li in range(2, 5)] + [pam["centroids"][33][li] for li in range(2, 5)]
        target = np.mean([m for m in mids if m is not None], axis=0) + shift
        src = np.concatenate(cord_sl).mean(0)
        scene["cord_streamlines"] = pack_lines(cord_sl, target - src)
        scene["stats"].update(cord_streamlines_total=int(sp.n_streamlines),
                              cord_streamlines_shown=len(cord_sl))
    return scene


def write_html(scene: dict, out_path: str | Path, template: str | Path = VIEWER_TEMPLATE) -> Path:
    html = Path(template).read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise ValueError(f"template {template} has no {PLACEHOLDER} placeholder")
    payload = json.dumps(scene, separators=(",", ":")).replace("</", "<\\/")
    out = Path(out_path)
    out.write_text(html.replace(PLACEHOLDER, payload), encoding="utf-8")
    return out
