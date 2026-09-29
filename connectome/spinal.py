"""Spinal cord: diffusion tractography on real cord DWI + PAM50 tract atlas.

Two independent measurements:

* ``tract_profiles`` - cross-sectional area (mm^2) of each PAM50 white-matter
  tract and grey-matter zone at each spinal segment C1..S5. PAM50 is a
  population template: the tract map is one histology-derived drawing warped
  along the cord, so per-level areas are a capacity proxy, not axon counts.
* ``dwi_run`` - DTI fit and deterministic tractography inside the cervical
  cord of the SCT example subject. At 0.9x0.9x5 mm individual tracts
  (e.g. lateral corticospinal vs rubrospinal) are not separable, so this
  gives cord-level microstructure/orientation, not tract identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy import ndimage

from .sources import SPINAL_LEVELS


def read_atlas_names(info_label: Path) -> dict[int, str]:
    names = {}
    for line in Path(info_label).read_text().splitlines():
        if "Keyword=CombinedLabels" in line:
            break
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[0].isdigit() and int(parts[0]) < 36:  # 36 = CSF
            names[int(parts[0])] = parts[1]
    return names


def tract_profiles(data_dir: str | Path) -> tuple[dict[int, np.ndarray], dict[int, str]]:
    """Return {atlas_id: area_mm2[30]} averaged over each segment's slices."""
    d = Path(data_dir) / "pam50"
    lv_img = nib.load(d / "PAM50_spinal_levels.nii.gz")
    levels = np.asarray(lv_img.dataobj).astype(int)
    vox_area = float(np.prod(lv_img.header.get_zooms()[:2]))
    z_of_level = [np.unique(np.nonzero(levels == k)[2]) for k in range(1, len(SPINAL_LEVELS) + 1)]
    names = read_atlas_names(d / "info_label.txt")
    profiles = {}
    for idx in names:
        prob = np.asarray(nib.load(d / f"PAM50_atlas_{idx:02d}.nii.gz").dataobj, np.float32)
        per_slice = prob.sum(axis=(0, 1)) * vox_area
        profiles[idx] = np.array([per_slice[z].mean() if len(z) else 0.0 for z in z_of_level])
    return profiles, names


@dataclass
class SpinalDWIResult:
    n_streamlines: int
    mean_length_mm: float
    longitudinal_fraction: float
    fa_per_slice: list[float]
    md_per_slice: list[float]
    cord_area_mm2_per_slice: list[float]
    qc: dict = field(default_factory=dict)
    streamlines: object = None
    affine: np.ndarray | None = None


def cord_mask_from_dwi(mean_dwi: np.ndarray, fa: np.ndarray) -> np.ndarray:
    """Crude cord segmentation: CSF is dark on DWI (b=800) while cord WM is
    bright and anisotropic. Keep the largest 3D component near the in-plane
    centre. SCT's deepseg would be the production-grade replacement."""
    from dipy.segment.threshold import otsu as threshold_otsu

    bright = mean_dwi > threshold_otsu(mean_dwi[mean_dwi > 0])
    cand = bright & (fa > 0.3)
    cand = ndimage.binary_opening(cand, structure=np.ones((2, 2, 1)))
    lab, n = ndimage.label(cand)
    if n == 0:
        return cand
    cx, cy = (np.array(mean_dwi.shape[:2]) - 1) / 2
    best, best_score = 0, -np.inf
    for k in range(1, n + 1):
        comp = lab == k
        size = comp.sum()
        com = ndimage.center_of_mass(comp)
        score = size - 5 * np.hypot(com[0] - cx, com[1] - cy)
        if score > best_score:
            best, best_score = k, score
    mask = lab == best
    return np.stack([ndimage.binary_fill_holes(mask[..., z]) for z in range(mask.shape[2])], -1)


def dwi_run(data_dir: str | Path, verbose: bool = True) -> SpinalDWIResult:
    from dipy.core.gradients import gradient_table
    from dipy.data import default_sphere
    from dipy.direction import peaks_from_model
    from dipy.io.gradients import read_bvals_bvecs
    from dipy.reconst.dti import TensorModel
    from dipy.tracking import utils
    from dipy.tracking.local_tracking import LocalTracking
    from dipy.tracking.stopping_criterion import BinaryStoppingCriterion
    from dipy.tracking.streamline import Streamlines

    log = print if verbose else (lambda *a, **k: None)
    d = Path(data_dir) / "spinal"
    img = nib.load(d / "dmri.nii.gz")
    data = np.asarray(img.dataobj, np.float32)
    bvals, bvecs = read_bvals_bvecs(str(d / "bvals.txt"), str(d / "bvecs.txt"))
    gtab = gradient_table(bvals, bvecs=bvecs, b0_threshold=50)
    zooms = img.header.get_zooms()[:3]
    log(f"[spinal] DWI {data.shape}, voxel {tuple(round(float(z), 2) for z in zooms)} mm")

    model = TensorModel(gtab)
    fit = model.fit(data)
    fa = np.clip(np.nan_to_num(fit.fa), 0, 1)
    md = np.nan_to_num(fit.md)
    mean_dwi = data[..., ~gtab.b0s_mask].mean(-1)
    cord = cord_mask_from_dwi(mean_dwi, fa)

    peaks = peaks_from_model(model, data, default_sphere, relative_peak_threshold=0.5,
                             min_separation_angle=25, mask=cord, npeaks=1)
    seeds = utils.seeds_from_mask(cord, img.affine, density=[2, 2, 1])
    sl = Streamlines(LocalTracking(peaks, BinaryStoppingCriterion(cord), seeds,
                                   img.affine, step_size=0.5))
    sl = Streamlines([s for s in sl if len(s) > 2])
    lengths = np.array([np.linalg.norm(np.diff(s, axis=0), axis=1).sum() for s in sl]) \
        if len(sl) else np.zeros(0)
    # which image axis is superior-inferior in this acquisition
    si_axis = int(np.argmax(np.abs(img.affine[2, :3])))
    evecs = fit.evecs[cord][..., 0]  # principal eigenvector per cord voxel (voxel axes)
    longitudinal = float(np.mean(np.abs(evecs[:, si_axis]) > 0.8)) if len(evecs) else 0.0

    px_area = float(zooms[0] * zooms[1])
    fa_z, md_z, area_z = [], [], []
    for z in range(cord.shape[2]):
        m = cord[..., z]
        fa_z.append(float(fa[..., z][m].mean()) if m.any() else float("nan"))
        md_z.append(float(md[..., z][m].mean() * 1e3) if m.any() else float("nan"))
        area_z.append(float(m.sum() * px_area))
    log(f"[spinal] cord area {np.nanmedian(area_z):.0f} mm^2/slice, FA {np.nanmean(fa_z):.2f}, "
        f"{len(sl)} streamlines, {100 * longitudinal:.0f}% voxels rostro-caudal")
    qc = {"voxel_size_mm": [float(z) for z in zooms], "cord_voxels": int(cord.sum()),
          "n_directions": int((~gtab.b0s_mask).sum()), "b_value": float(bvals.max())}
    return SpinalDWIResult(len(sl), float(lengths.mean()) if len(lengths) else 0.0,
                           longitudinal, fa_z, md_z, area_z, qc, sl, img.affine)
