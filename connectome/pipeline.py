"""End-to-end: download -> brain DTI -> spinal DTI + PAM50 -> graph -> exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import networkx as nx
import numpy as np

from . import brain, graph, spinal
from .download import download_all
from .sources import SPINAL_LEVELS

BLUE, ORANGE, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df"


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def figures(out: Path, br, sp, profiles, names):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if br is not None:
        # order: left cortex, right cortex, subcortex/brainstem
        order = ([i for i, r in enumerate(br.labels) if r["kind"] == "cortex" and r["hemi"] == "L"]
                 + [i for i, r in enumerate(br.labels) if r["kind"] == "cortex" and r["hemi"] == "R"]
                 + [i for i, r in enumerate(br.labels) if r["kind"] != "cortex"])
        M = np.log10(1 + br.matrix[np.ix_(order, order)])
        fig, ax = plt.subplots(figsize=(9, 8))
        im = ax.imshow(M, cmap="Blues", interpolation="nearest")
        ax.set_title(f"Brain structural connectome - {br.n_streamlines:,} streamlines "
                     f"(ds000114 sub-01, CSA-ODF deterministic)", fontsize=10, color=INK, loc="left")
        for b in (48, 96):
            ax.axhline(b - 0.5, color=MUTED, lw=0.6)
            ax.axvline(b - 0.5, color=MUTED, lw=0.6)
        ax.set_xticks([24, 72, 103], ["L cortex", "R cortex", "subcortex"], fontsize=8)
        ax.set_yticks([24, 72, 103], ["L cortex", "R cortex", "subcortex"], fontsize=8)
        cb = fig.colorbar(im, ax=ax, shrink=0.7)
        cb.set_label("log10(1 + streamline count)", fontsize=8, color=MUTED)
        fig.tight_layout()
        fig.savefig(out / "brain_dti_matrix.png", dpi=130)
        plt.close(fig)

    # PAM50 tract area profiles (left side), ascending vs descending
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    x = np.arange(len(SPINAL_LEVELS))
    for ax, direction, color, title in ((axes[0], "down", ORANGE, "Descending (output) tracts"),
                                        (axes[1], "up", BLUE, "Ascending (input) tracts")):
        _style(ax)
        tr = [t for t in graph.TRACTS if t["dir"] == direction]
        shades = np.linspace(1.0, 0.35, len(tr))
        placed = []  # direct-label only where labels would not collide
        for t, a in sorted(zip(tr, shades), key=lambda p: -profiles[p[0]["ids"][0]][0]):
            y = profiles[t["ids"][0]]
            ax.plot(x, y, color=color, alpha=a, lw=2)
            if all(abs(y[0] - q) > 0.3 for q in placed):
                placed.append(y[0])
                ax.annotate(t["name"], (x[0], y[0]), xytext=(-4, 0), textcoords="offset points",
                            ha="right", va="center", fontsize=6.5, color=MUTED)
        hidden = len(tr) - len(placed)
        if hidden:
            ax.annotate(f"+{hidden} brainstem tracts\n(reticulo/vestibulo/tecto, MLF)",
                        (x[0], 1.4), xytext=(-4, 0), textcoords="offset points",
                        ha="right", va="center", fontsize=6.5, color=MUTED)
        ax.set_title(title, fontsize=10, color=INK, loc="left")
        ax.set_xticks(x[::2], SPINAL_LEVELS[::2], fontsize=7)
        ax.set_xlim(-9, len(x))
    axes[0].set_ylabel("tract cross-section, left side (mm$^2$, PAM50)", fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(out / "spinal_tract_profiles.png", dpi=130)
    plt.close(fig)

    if sp is not None:
        fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
        z = np.arange(len(sp.fa_per_slice))
        for ax, vals, lab in ((axes[0], sp.cord_area_mm2_per_slice, "cord area (mm$^2$)"),
                              (axes[1], sp.fa_per_slice, "FA"),
                              (axes[2], sp.md_per_slice, "MD (10$^{-3}$ mm$^2$/s)")):
            _style(ax)
            ax.plot(z, vals, color=BLUE, lw=2, marker="o", ms=4)
            ax.set_title(lab, fontsize=10, color=INK, loc="left")
            ax.set_xlabel("slice (5 mm, cervical cord)", fontsize=8, color=MUTED)
        fig.suptitle(f"Spinal cord DTI (SCT example subject): {sp.n_streamlines:,} streamlines, "
                     f"{100 * sp.longitudinal_fraction:.0f}% of cord voxels rostro-caudal",
                     fontsize=10, color=INK, x=0.01, ha="left")
        fig.tight_layout()
        fig.savefig(out / "spinal_dwi_qc.png", dpi=130)
        plt.close(fig)


def export(G: nx.MultiDiGraph, out: Path):
    nx.write_graphml(G, out / "connectome.graphml")
    node_keys = sorted({k for _, d in G.nodes(data=True) for k in d})
    with open(out / "nodes.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", *node_keys])
        for n, d in G.nodes(data=True):
            w.writerow([n, *[d.get(k, "") for k in node_keys]])
    with open(out / "edges.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "target", "provenance", "weight", "directed", "pathway"])
        for u, v, d in G.edges(data=True):
            if d["provenance"] == "dti_brain" and u > v:
                continue  # undirected DTI edge: write once
            w.writerow([u, v, d["provenance"], round(d["weight"], 4), d["directed"], d["pathway"]])


ENV_PACKAGES = ("dipy", "nibabel", "numpy", "scipy", "networkx", "matplotlib")


def environment() -> dict:
    """Library versions and platform, so a run can be tied to its setup.
    Tractography counts shift between dipy/numpy/scipy versions."""
    import platform
    from importlib.metadata import PackageNotFoundError, version

    env = {"python": platform.python_version(), "platform": platform.platform()}
    for pkg in ENV_PACKAGES:
        try:
            env[pkg] = version(pkg)
        except PackageNotFoundError:
            env[pkg] = None
    return env


def run(data_dir="data/connectome", out_dir="results/connectome", subject="01",
        skip_brain=False, skip_spinal_dwi=False, save_tractograms=False, viewer_3d=True,
        verbose=True):
    data_dir, out = Path(data_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    download_all(data_dir, subject=subject, verbose=verbose)

    br = None if skip_brain else brain.run(data_dir, verbose=verbose)
    sp = None if skip_spinal_dwi else spinal.dwi_run(data_dir, verbose=verbose)
    profiles, names = spinal.tract_profiles(data_dir)

    G = graph.build(br.labels if br else None, br.matrix if br else None, profiles)
    export(G, out)
    figures(out, br, sp, profiles, names)
    if viewer_3d and br is not None:
        from . import scene
        path = scene.write_html(scene.build_scene(data_dir, G, br, sp), out / "connectome3d.html")
        if verbose:
            print(f"[3d] wrote {path} ({path.stat().st_size / 1e6:.1f} MB); open it in a browser")
    elif viewer_3d and verbose:
        print("[3d] skipped: the 3D viewer needs the brain tractography (drop --skip-brain)")

    if br is not None:
        ids = [r["id"] for r in br.labels]
        with open(out / "brain_dti_matrix.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["region", *ids])
            for i, row in enumerate(br.matrix):
                w.writerow([ids[i], *[int(v) for v in row]])
    with open(out / "spinal_tract_areas_mm2.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["atlas_id", "structure", *SPINAL_LEVELS])
        for idx in sorted(profiles):
            w.writerow([idx, names[idx], *[round(float(a), 3) for a in profiles[idx]]])

    if save_tractograms:
        from dipy.io.stateful_tractogram import Space, StatefulTractogram
        from dipy.io.streamline import save_trk
        import nibabel as nib
        for tag, res, ref in (("brain", br, data_dir / "brain/dwi.nii.gz"),
                              ("spinal", sp, data_dir / "spinal/dmri.nii.gz")):
            if res is not None:
                sft = StatefulTractogram(res.streamlines, nib.load(ref), Space.RASMM)
                save_trk(sft, str(out / f"{tag}_tractogram.trk"), bbox_valid_check=False)

    summary = graph.summarize(G)
    summary["environment"] = environment()
    summary["brain_qc"] = br.qc if br else None
    summary["brain_streamlines"] = br.n_streamlines if br else None
    if sp is not None:
        summary["spinal_dwi"] = {
            "n_streamlines": sp.n_streamlines, "mean_length_mm": round(sp.mean_length_mm, 1),
            "longitudinal_fraction": round(sp.longitudinal_fraction, 3),
            "mean_fa": round(float(np.nanmean(sp.fa_per_slice)), 3),
            "median_cord_area_mm2": round(float(np.nanmedian(sp.cord_area_mm2_per_slice)), 1),
            **sp.qc}
    examples = {
        "L5 left touch -> cortex": ("L5 L DRG", "Postcentral Gyrus (R)"),
        "Right motor cortex -> C6 left muscle": ("Precentral Gyrus (R)", "C6 L motor output"),
        "C7 right-arm sensation -> thalamus": ("C7 R DRG", "Thalamus (L)"),
        "Retina -> neck muscles (orienting)": ("CN II retinal ganglion cells", "C2 L motor output"),
    }
    summary["example_paths"] = {k: graph.reachability(G, *v) for k, v in examples.items()}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    if verbose:
        print(json.dumps({k: summary[k] for k in ("nodes", "edges", "roles", "edges_by_provenance")},
                         indent=2))
    return G, summary
