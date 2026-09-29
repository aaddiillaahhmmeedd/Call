# Brain + spinal cord connectome

`python -m connectome build` downloads public diffusion MRI and atlas data,
runs tractography, and writes a directed graph that runs from peripheral
**input** neurons (dorsal root ganglia, cranial sensory ganglia) through the
spinal cord, brainstem, thalamus and cortex, and back out to **output**
neurons (motor, sympathetic and parasympathetic outflow, cranial motor
nerves).

```bash
pip install -e ".[connectome]"
python -m connectome download            # ~110 MB into data/connectome/
python -m connectome build               # ~3 min on 4 CPUs -> results/connectome/
python -m connectome build --subject 05 --save-tractograms   # other subject + .trk files
```

The build also writes `results/connectome/connectome3d.html`, a self-contained
3D viewer (open it in any browser; it loads three.js from a CDN). It shows
4,000 sampled brain streamlines, the cervical cord tractography, PAM50 tract
paths, every node, and traces input-to-output pathways as a highlighted route.
Pass `--no-3d` to skip it.

## Data (all public, pinned)

| Part | Source | What it gives |
|---|---|---|
| Brain DWI | OpenNeuro **ds000114** sub-01 (CC0), 2 mm iso, 64 dirs, b=1000 | whole-brain tractography |
| Brain atlas | TemplateFlow MNI152NLin2009cAsym + Harvard-Oxford cortical/subcortical | 111 regions incl. brainstem |
| Spinal DWI | SCT `sct_example_data` (pinned commit), 0.9x0.9x5 mm, 30 dirs, b=800 | cervical cord DTI + tractography |
| Spinal atlas | **PAM50** template + white/grey matter atlas (pinned commit) | 30 tracts and 6 grey-matter zones, C1-S5 |

## Output (`results/connectome/`)

- `connectome.graphml` - the full graph (open in Gephi/Cytoscape/networkx)
- `nodes.csv` - `role` is `input`, `output`, `relay` or `interneuron_pool`
- `edges.csv` - every edge has a `provenance`:
  - `dti_brain` - streamline count from this subject (undirected)
  - `pam50_tract` - spinal tract present at that segment; weight = tract area (mm^2)
  - `anatomy` - textbook relay with known direction and crossing (weight 1)
- `connectome3d.html` - interactive 3D viewer (see above). Brain and cord
  come from different sources: the PAM50 cord is attached at the lowest
  brainstem slice and the second subject's cord tractography is shifted onto
  C1-C5 (translations only). Brainstem nuclei, cerebellum, cranial nerves and
  peripheral pools are placed schematically; each node says which.
- `brain_dti_matrix.csv/.png`, `spinal_tract_areas_mm2.csv`,
  `spinal_tract_profiles.png`, `spinal_dwi_qc.png`, `summary.json`
  (includes the Python/dipy/numpy/scipy versions that produced the run)

## What this is not

- **Not neuron-level.** DTI resolves millimetre fibre bundles. Nodes are
  regions and neuron *pools*; nothing here is a synapse-resolution wiring
  diagram. A human neuron-level connectome does not exist yet for any
  part of the CNS larger than ~1 mm^3 of cortex.
- **DTI has no direction.** Afferent vs efferent comes from the `anatomy`
  and `pam50_tract` layers, not from the scan.
- **Brain and spinal cord are different people.** No public dataset
  reachable here images brain and full cord in one subject with DWI.
  They are joined through atlas-defined brainstem nuclei.
- **Spinal tract identity is atlas-based.** At 0.9x0.9x5 mm the cord DTI
  confirms longitudinal white matter (99% of cord voxels rostro-caudal,
  cord area ~79 mm^2) but cannot separate e.g. corticospinal from
  rubrospinal fibres. PAM50 tract areas are one template, not a
  per-subject measurement.
- **Counts depend on library versions.** The same data run on Windows with a
  different dipy/numpy/scipy stack gave 45,383 brain streamlines vs 50,600
  here (spinal: 5,389 vs 5,408); anatomy and PAM50 edges were identical.
  Compare `environment` in `summary.json` before comparing runs.
- **Deterministic tractography is noisy.** ~46% of region pairs have >= 1
  streamline; threshold on `weight` before doing graph statistics, and
  expect false positives/negatives (interhemispheric lateral cortex is
  under-represented).
