"""Whole-brain structural connectome from diffusion MRI.

Pipeline: brain mask -> DTI (FA) -> register MNI T2w template onto the mean
b0 (affine + SyN) -> warp Harvard-Oxford labels into diffusion space ->
CSA-ODF deterministic tractography -> streamline-count connectivity matrix.

Tractography is undirected: it cannot tell afferent from efferent fibres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy import ndimage

from .sources import HO_CORTICAL, HO_SUBCORTICAL


@dataclass
class BrainResult:
    labels: list[dict]                 # one dict per matrix row/col
    matrix: np.ndarray                 # symmetric streamline counts
    n_streamlines: int
    fa: np.ndarray
    affine: np.ndarray
    label_img: np.ndarray
    qc: dict = field(default_factory=dict)
    streamlines: object = None


def build_label_table() -> list[dict]:
    """Node table in matrix order: cortical L/R, then subcortical."""
    table = []
    for i, name in enumerate(HO_CORTICAL, start=1):
        for hemi in ("L", "R"):
            table.append({"name": name, "hemi": hemi, "kind": "cortex",
                          "src": ("HOCPA", i)})
    for i, (name, hemi) in HO_SUBCORTICAL.items():
        kind = "brainstem" if name == "Brain-Stem" else "subcortex"
        table.append({"name": name, "hemi": hemi, "kind": kind, "src": ("HOSPA", i)})
    for k, row in enumerate(table, start=1):
        row["index"] = k
        row["id"] = f"{row['name']} ({row['hemi']})" if row["hemi"] != "M" else row["name"]
    return table


def combine_mni_atlas(cort: nib.Nifti1Image, sub: nib.Nifti1Image,
                      table: list[dict]) -> np.ndarray:
    """One integer label volume in MNI space, cortex split by hemisphere."""
    c = np.asarray(cort.dataobj).astype(int)
    s = np.asarray(sub.dataobj).astype(int)
    ijk = np.indices(c.shape).reshape(3, -1)
    x = (cort.affine[:3, :3] @ ijk + cort.affine[:3, 3:4])[0].reshape(c.shape)
    out = np.zeros(c.shape, np.int32)
    for row in table:
        atlas, idx = row["src"]
        if atlas == "HOCPA":
            side = x < 0 if row["hemi"] == "L" else x >= 0
            out[(c == idx) & side] = row["index"]
    for row in table:  # subcortical wins where the two atlases overlap
        atlas, idx = row["src"]
        if atlas == "HOSPA":
            out[s == idx] = row["index"]
    return out


def grow_labels(labels: np.ndarray, mask: np.ndarray, iterations: int = 2) -> np.ndarray:
    """Dilate labels into unlabeled in-mask voxels so streamlines that stop
    at the grey/white boundary still land on a region."""
    out = labels.copy()
    for _ in range(iterations):
        grown = ndimage.grey_dilation(out, size=(3, 3, 3))
        fill = (out == 0) & (grown > 0) & mask
        out[fill] = grown[fill]
    return out


def run(data_dir: str | Path, *, syn: bool = True, seed_density: int = 1,
        fa_seed: float = 0.3, fa_stop: float = 0.15, min_length_mm: float = 20.0,
        verbose: bool = True) -> BrainResult:
    from dipy.align import affine_registration, syn_registration
    from dipy.align.imaffine import AffineMap
    from dipy.core.gradients import gradient_table
    from dipy.data import default_sphere
    from dipy.direction import peaks_from_model
    from dipy.io.gradients import read_bvals_bvecs
    from dipy.reconst.dti import TensorModel
    from dipy.reconst.shm import CsaOdfModel
    from dipy.segment.mask import median_otsu
    from dipy.tracking import utils
    from dipy.tracking.local_tracking import LocalTracking
    from dipy.tracking.stopping_criterion import ThresholdStoppingCriterion
    from dipy.tracking.streamline import Streamlines

    log = print if verbose else (lambda *a, **k: None)
    d = Path(data_dir)
    img = nib.load(d / "brain/dwi.nii.gz")
    data = np.asarray(img.dataobj, dtype=np.float32)
    affine = img.affine
    bvals, bvecs = read_bvals_bvecs(str(d / "brain/dwi.bval"), str(d / "brain/dwi.bvec"))
    gtab = gradient_table(bvals, bvecs=bvecs)
    log(f"[brain] DWI {data.shape}, {int((~gtab.b0s_mask).sum())} directions")

    b0 = data[..., gtab.b0s_mask].mean(-1)
    _, mask = median_otsu(b0, median_radius=2, numpass=1)
    mask = ndimage.binary_fill_holes(mask)

    tenfit = TensorModel(gtab).fit(data, mask=mask)
    fa = np.clip(np.nan_to_num(tenfit.fa), 0, 1)
    log(f"[brain] mask {int(mask.sum())} vox, median WM FA "
        f"{np.median(fa[fa > 0.2]):.2f}")

    # --- atlas into diffusion space -----------------------------------
    t2 = nib.load(d / "mni/T2w.nii.gz")
    t2_brain = np.asarray(t2.dataobj, np.float32) * (
        np.asarray(nib.load(d / "mni/brain_mask.nii.gz").dataobj) > 0.5)
    static = b0 * mask
    log("[brain] affine registration MNI T2w -> b0 ...")
    warped, reg_affine = affine_registration(
        t2_brain, static, moving_affine=t2.affine, static_affine=affine,
        pipeline=["center_of_mass", "translation", "rigid", "affine"],
        level_iters=[10000, 1000, 100])
    amap = AffineMap(reg_affine, domain_grid_shape=static.shape, domain_grid2world=affine,
                     codomain_grid_shape=t2_brain.shape, codomain_grid2world=t2.affine)
    table = build_label_table()
    mni_labels = combine_mni_atlas(nib.load(d / "mni/HOCPA_th25.nii.gz"),
                                   nib.load(d / "mni/HOSPA_th25.nii.gz"), table)
    if syn:
        log("[brain] SyN registration ...")
        pre = amap.transform(t2_brain)
        _, mapping = syn_registration(pre, static, moving_affine=affine,
                                      static_affine=affine, level_iters=[10, 10, 5])
        pre_lab = amap.transform(mni_labels.astype(np.float32), interpolation="nearest")
        labels = mapping.transform(pre_lab, interpolation="nearest").astype(np.int32)
        warped = mapping.transform(pre)
    else:
        labels = amap.transform(mni_labels.astype(np.float32),
                                interpolation="nearest").astype(np.int32)
    labels[~mask] = 0
    wm_mask = mask & (fa > fa_stop)
    labels = grow_labels(labels, mask, iterations=2)
    tpl_mask = warped > 0.1 * warped.max()
    dice = float(2 * (tpl_mask & mask).sum() / (tpl_mask.sum() + mask.sum()))
    log(f"[brain] template/DWI brain-mask Dice after registration: {dice:.2f}")

    # --- tractography -------------------------------------------------
    csa = CsaOdfModel(gtab, sh_order_max=6)
    peaks = peaks_from_model(csa, data, default_sphere, relative_peak_threshold=0.5,
                             min_separation_angle=25, mask=wm_mask, npeaks=3)
    stop = ThresholdStoppingCriterion(fa, fa_stop)
    seeds = utils.seeds_from_mask(fa > fa_seed, affine, density=seed_density)
    log(f"[brain] tracking from {len(seeds)} seeds ...")
    sl = Streamlines(LocalTracking(peaks, stop, seeds, affine, step_size=0.5))
    lengths = np.array([np.sum(np.linalg.norm(np.diff(s, axis=0), axis=1)) for s in sl])
    sl = Streamlines([s for s, L in zip(sl, lengths) if L >= min_length_mm])
    log(f"[brain] {len(sl)} streamlines >= {min_length_mm} mm")

    n = len(table)
    M = utils.connectivity_matrix(sl, affine, labels, symmetric=True, mapping_as_streamlines=False)
    M = np.asarray(M, dtype=float)
    if M.shape[0] < n + 1:
        M = np.pad(M, ((0, n + 1 - M.shape[0]), (0, n + 1 - M.shape[1])))
    M = M[1:n + 1, 1:n + 1]
    np.fill_diagonal(M, 0)

    present = {int(v) for v in np.unique(labels) if v}
    qc = {"registration_dice": dice, "median_wm_fa": float(np.median(fa[fa > 0.2])),
          "regions_present": len(present), "regions_total": n,
          "missing_regions": [r["id"] for r in table if r["index"] not in present],
          "voxel_size_mm": [float(z) for z in img.header.get_zooms()[:3]]}
    return BrainResult(table, M, len(sl), fa, affine, labels, qc, sl)
