"""Pinned public data sources and label tables used by the connectome pipeline.

Every URL points at an immutable object (an OpenNeuro S3 snapshot, a
TemplateFlow S3 object, or a GitHub raw file pinned to a commit) so reruns
download byte-identical inputs.
"""

from __future__ import annotations

OPENNEURO = "https://s3.amazonaws.com/openneuro.org"
TEMPLATEFLOW = "https://templateflow.s3.amazonaws.com/tpl-MNI152NLin2009cAsym"
PAM50_RAW = (
    "https://raw.githubusercontent.com/spinalcordtoolbox/PAM50/"
    "609a18235d9143dad0d6bf5bb35e2bff9e55196f"
)
SCT_EXAMPLE_RAW = (
    "https://raw.githubusercontent.com/spinalcordtoolbox/sct_example_data/"
    "9efd66be3aed1e317e487b8027a6bd83262c1aa8"
)

# OpenNeuro ds000114 (test-retest, 10 healthy adults, CC0): 2x2x2 mm,
# 64 directions at b=1000 + 7 b0. bval/bvec live at the dataset root (BIDS
# inheritance) because they are identical for every subject.
BRAIN_DATASET = "ds000114"


def brain_files(subject: str = "01", session: str = "test") -> dict[str, str]:
    base = f"{OPENNEURO}/{BRAIN_DATASET}"
    dwi = f"sub-{subject}/ses-{session}/dwi/sub-{subject}_ses-{session}_dwi.nii.gz"
    return {
        "brain/dwi.nii.gz": f"{base}/{dwi}",
        "brain/dwi.bval": f"{base}/dwi.bval",
        "brain/dwi.bvec": f"{base}/dwi.bvec",
    }


TEMPLATE_FILES = {
    "mni/T2w.nii.gz": f"{TEMPLATEFLOW}/tpl-MNI152NLin2009cAsym_res-02_T2w.nii.gz",
    "mni/brain_mask.nii.gz": f"{TEMPLATEFLOW}/tpl-MNI152NLin2009cAsym_res-02_desc-brain_mask.nii.gz",
    "mni/HOCPA_th25.nii.gz": f"{TEMPLATEFLOW}/tpl-MNI152NLin2009cAsym_res-02_atlas-HOCPA_desc-th25_dseg.nii.gz",
    "mni/HOSPA_th25.nii.gz": f"{TEMPLATEFLOW}/tpl-MNI152NLin2009cAsym_res-02_atlas-HOSPA_desc-th25_dseg.nii.gz",
}

# SCT example data: cervical spinal cord DWI, 0.9x0.9x5 mm, 30 dirs b=800.
SPINAL_DWI_FILES = {
    "spinal/dmri.nii.gz": f"{SCT_EXAMPLE_RAW}/dmri/dmri.nii.gz",
    "spinal/bvals.txt": f"{SCT_EXAMPLE_RAW}/dmri/bvals.txt",
    "spinal/bvecs.txt": f"{SCT_EXAMPLE_RAW}/dmri/bvecs.txt",
}

# PAM50 spinal cord template + probabilistic white/gray matter tract atlas.
PAM50_FILES = {
    "pam50/PAM50_spinal_levels.nii.gz": f"{PAM50_RAW}/template/PAM50_spinal_levels.nii.gz",
    "pam50/info_label.txt": f"{PAM50_RAW}/atlas/info_label.txt",
    **{
        f"pam50/PAM50_atlas_{i:02d}.nii.gz": f"{PAM50_RAW}/atlas/PAM50_atlas_{i:02d}.nii.gz"
        for i in range(36)
    },
}


def all_files(subject: str = "01", session: str = "test") -> dict[str, str]:
    return {**brain_files(subject, session), **TEMPLATE_FILES, **SPINAL_DWI_FILES, **PAM50_FILES}


# Harvard-Oxford cortical atlas (FSL ordering, labels 1..48). Hemisphere is
# assigned afterwards from the MNI x coordinate.
HO_CORTICAL = [
    "Frontal Pole", "Insular Cortex", "Superior Frontal Gyrus",
    "Middle Frontal Gyrus", "Inferior Frontal Gyrus, pars triangularis",
    "Inferior Frontal Gyrus, pars opercularis", "Precentral Gyrus",
    "Temporal Pole", "Superior Temporal Gyrus, anterior division",
    "Superior Temporal Gyrus, posterior division",
    "Middle Temporal Gyrus, anterior division",
    "Middle Temporal Gyrus, posterior division",
    "Middle Temporal Gyrus, temporooccipital part",
    "Inferior Temporal Gyrus, anterior division",
    "Inferior Temporal Gyrus, posterior division",
    "Inferior Temporal Gyrus, temporooccipital part", "Postcentral Gyrus",
    "Superior Parietal Lobule", "Supramarginal Gyrus, anterior division",
    "Supramarginal Gyrus, posterior division", "Angular Gyrus",
    "Lateral Occipital Cortex, superior division",
    "Lateral Occipital Cortex, inferior division", "Intracalcarine Cortex",
    "Frontal Medial Cortex", "Juxtapositional Lobule Cortex (SMA)",
    "Subcallosal Cortex", "Paracingulate Gyrus",
    "Cingulate Gyrus, anterior division", "Cingulate Gyrus, posterior division",
    "Precuneous Cortex", "Cuneal Cortex", "Frontal Orbital Cortex",
    "Parahippocampal Gyrus, anterior division",
    "Parahippocampal Gyrus, posterior division", "Lingual Gyrus",
    "Temporal Fusiform Cortex, anterior division",
    "Temporal Fusiform Cortex, posterior division",
    "Temporal Occipital Fusiform Cortex", "Occipital Fusiform Gyrus",
    "Frontal Operculum Cortex", "Central Opercular Cortex",
    "Parietal Operculum Cortex", "Planum Polare",
    "Heschl's Gyrus (includes H1 and H2)", "Planum Temporale",
    "Supracalcarine Cortex", "Occipital Pole",
]

# Harvard-Oxford subcortical atlas (FSL ordering, labels 1..21). White
# matter, cortex and ventricle labels are dropped (None).
HO_SUBCORTICAL = {
    4: ("Thalamus", "L"), 5: ("Caudate", "L"), 6: ("Putamen", "L"),
    7: ("Pallidum", "L"), 8: ("Brain-Stem", "M"), 9: ("Hippocampus", "L"),
    10: ("Amygdala", "L"), 11: ("Accumbens", "L"),
    15: ("Thalamus", "R"), 16: ("Caudate", "R"), 17: ("Putamen", "R"),
    18: ("Pallidum", "R"), 19: ("Hippocampus", "R"), 20: ("Amygdala", "R"),
    21: ("Accumbens", "R"),
}

# PAM50 spinal level labels 1..30.
SPINAL_LEVELS = (
    [f"C{i}" for i in range(1, 9)]
    + [f"T{i}" for i in range(1, 13)]
    + [f"L{i}" for i in range(1, 6)]
    + [f"S{i}" for i in range(1, 6)]
)
