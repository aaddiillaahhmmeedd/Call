# Data sources for a brain + spinal cord connectome

A survey of what exists (September 2026), what is open, and what this
pipeline uses. "Reachable here" means reachable from the sandbox that built
`results/connectome/`; everything marked open can be fetched on an ordinary
internet connection.

## What the pipeline uses

| Layer | Source | Access | What it contributes |
|---|---|---|---|
| Brain DWI, one subject | [OpenNeuro ds000114](https://openneuro.org/datasets/ds000114) sub-01 test | CC0, S3 | default single-scan build |
| Brain DWI, group | ds000114, **all 10 subjects × test/retest = 20 scans** (`--group`) | CC0, S3 | consensus edges + test/retest reliability |
| Cortical/subcortical parcels | Harvard-Oxford via [TemplateFlow](https://www.templateflow.org) MNI152NLin2009cAsym | open, S3 | 111 regions |
| Midbrain/brainstem nuclei | **MASSP** (Bazin et al. 2020, 7T) via TemplateFlow | open, S3 | red nucleus, substantia nigra, STN, superior/inferior colliculi, PAG, PPN, VTA as measured regions |
| Cerebellum | FreeSurfer **aseg** on the template, via TemplateFlow | open, S3 | cerebellum L/R as measured regions |
| Spinal cord DWI | [SCT example data](https://github.com/spinalcordtoolbox/sct_example_data) | MIT, GitHub | cervical cord tractography, one subject |
| Spinal tracts + grey matter | [PAM50](https://github.com/spinalcordtoolbox/PAM50) (De Leener et al. 2018) | open, GitHub | 30 tracts, 6 grey-matter zones, C1–S5 |

The same cervical cord DWI also ships in
[`sct_tutorial_data`](https://github.com/spinalcordtoolbox/sct_tutorial_data)
(identical file, same MD5), so it does not add a second cord subject.

## Open, not integrated — candidates for the next step

### Brain diffusion MRI
| Source | Size | Why it matters | Access |
|---|---|---|---|
| [HCP Young Adult 1200](https://www.humanconnectome.org/study/hcp-young-adult) | 1,065 subjects, 1.25 mm, 3 shells | the reference dataset | free, **requires accepting data-use terms**; AWS credentials |
| [HCP-1065 tract atlas (Yeh 2022)](https://brain.labsolver.org/hcp_trk_atlas.html) | population tract probabilities incl. corticospinal, medial lemniscus, cerebellar peduncles | would replace atlas-prior brainstem routes with population tractography | open; GitHub release assets ([frankyeh/data-atlas](https://github.com/frankyeh/data-atlas/releases)) — release downloads blocked in this sandbox |
| [Connectome 2.0, ds006181](https://openneuro.org/datasets/ds006181) | 500 mT/m gradients | highest-resolution in-vivo DWI public | CC0, S3 |
| [MASiVar, ds003416](https://openneuro.org/datasets/ds003416) | multi-site/-scanner | scanner variability | CC0, S3 |
| [Multi-b-value test-retest, ds008695](https://github.com/OpenNeuroDatasets/ds008695) | 4 sessions | longitudinal reliability | CC0 |
| [CoRR / NKI-Rockland (fcp-indi)](https://fcp-indi.github.io) | hundreds of DTI test-retest scans | reliability at scale | open, S3 (reachable here) |
| [dHCP](http://www.developingconnectome.org/) | 558 neonates | developing connectome | free, data-use terms |
| [HCP-Aging population connectome](https://pmc.ncbi.nlm.nih.gov/articles/PMC10474320/) | 422 subjects, pre-computed | ready-made group matrix | open |

### Brainstem
| Source | What | Access |
|---|---|---|
| Brainstem Navigator (Bianciardi lab; [template paper](https://journals.sagepub.com/doi/abs/10.1089/brain.2015.0347)) | 7T probabilistic atlas of ~58 brainstem nuclei incl. **vestibular nuclei complex, inferior olive, reticular nuclei, parabrachial** | open on NITRC (blocked here) — would replace the last 5 schematic nuclei |
| [Probabilistic atlas of brainstem pathways (Tang et al. 2018)](https://pubmed.ncbi.nlm.nih.gov/29253653/) | HCP-derived brainstem tract probabilities | paper supplement |
| [Subcortical nuclei atlas (Pauli 2018)](https://www.nature.com/articles/sdata201863) | 7T subcortical nuclei incl. red nucleus | NeuroVault (blocked here) |

### Spinal cord
| Source | What | Access |
|---|---|---|
| [spine-generic multi-subject](https://github.com/spine-generic/data-multi-subject) | **267 people, 43 sites, cervical DWI** + T1/T2/T2*/MT | open via git-annex; annexed files served from `data.neuro.polymtl.ca` (blocked here; works on a normal connection with `git annex get`) |
| [spine-generic single-subject](https://github.com/spine-generic/data-single-subject) | one person scanned at 19 sites | same |

With spine-generic the cord side would get the same treatment the brain now
has: per-level DTI metrics averaged over hundreds of people instead of one.

## Neuron-level connectomes — the honest ceiling

Nothing above resolves neurons. These do, and none of them is a human brain
plus spinal cord:

| Dataset | Scale | Input → output coverage |
|---|---|---|
| [BANC, *Drosophila* brain and nerve cord](https://blog.flywire.ai/2025/11/03/the-banc-brain-and-nerve-cord/) (Nature) | ~160,000 neurons, synapse level | **complete: sensory neurons through to motor neurons** — the only whole-CNS input/output connectome of an adult animal |
| [MANC, male adult nerve cord](https://www.janelia.org/news/researchers-reveal-connectome-of-the-male-fruit-fly-central-nervous-system) | fly ventral nerve cord | descending input to motor output |
| [H01, human temporal cortex](https://h01-release.storage.googleapis.com/landing.html) | ~1 mm³, 57,000 cells, 150 M synapses | a cortex fragment; no inputs or outputs in the sense used here (reachable here) |
| MICrONS (mouse visual cortex, BossDB) | ~1 mm³ | same limitation |

If the goal is a real neuron-to-neuron map from sensory input to motor
output, BANC is the dataset that exists. For humans the gap between the
~1 mm³ of H01 and a whole brain plus cord is roughly six orders of magnitude
in volume.
