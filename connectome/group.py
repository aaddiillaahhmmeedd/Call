"""Group brain connectome: every ds000114 scan (10 adults x test/retest).

One subject's deterministic tractography is noisy (weak edges come and go
with library versions). Running every scan lets each edge be judged by how
consistently it appears, and the test/retest pairs measure how reliable the
per-subject matrices are.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import brain
from .download import fetch
from .sources import BRAIN_SESSIONS, BRAIN_SUBJECTS, brain_files, scan_prefix


def scan_list(subjects=BRAIN_SUBJECTS, sessions=BRAIN_SESSIONS) -> list[tuple[str, str]]:
    return [(s, ses) for s in subjects for ses in sessions]


def run_scan(data_dir, subject: str, session: str, *, verbose=True, keep_result=False):
    """Download one scan, run the brain pipeline, cache the matrix.
    Returns (matrix, qc, label_ids[, BrainResult])."""
    d = Path(data_dir)
    cache = d / "cache" / f"sub-{subject}_ses-{session}.npz"
    if cache.exists() and not keep_result:
        z = np.load(cache, allow_pickle=False)
        return z["matrix"], json.loads(str(z["qc"])), list(z["ids"])
    prefix = scan_prefix(subject, session)
    for rel, url in brain_files(subject, session, prefix=prefix).items():
        fetch(url, d / rel)
    if verbose:
        print(f"[group] sub-{subject} ses-{session}")
    r = brain.run(d, scan_dir=prefix, verbose=False)
    ids = [x["id"] for x in r.labels]
    qc = {**r.qc, "n_streamlines": int(r.n_streamlines)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, matrix=r.matrix, qc=json.dumps(qc), ids=np.array(ids))
    if verbose:
        print(f"[group]   {r.n_streamlines} streamlines, Dice {qc['registration_dice']:.2f}, "
              f"{qc['regions_present']}/{qc['regions_total']} regions")
    return (r.matrix, qc, ids, r) if keep_result else (r.matrix, qc, ids)


def consensus(mats: np.ndarray, min_streamlines: float = 5, min_fraction: float = 0.5):
    """Edge kept if it has >= ``min_streamlines`` in >= ``min_fraction`` of
    scans. Weight = median streamline count over scans where it is present.
    Returns (weights, consistency) with zeros on dropped edges."""
    present = mats >= min_streamlines
    consistency = present.mean(0)
    masked = np.where(present, mats, np.nan)
    with np.errstate(all="ignore"):
        med = np.nan_to_num(np.nanmedian(masked, axis=0))
    keep = consistency >= min_fraction
    np.fill_diagonal(keep, False)
    return np.where(keep, med, 0.0), np.where(keep, consistency, 0.0)


def upper(m: np.ndarray) -> np.ndarray:
    return m[np.triu_indices_from(m, 1)]


def reliability(mats: dict[tuple[str, str], np.ndarray]) -> dict:
    """Test/retest similarity of log(1 + streamlines) over all region pairs:
    same person across sessions vs different people."""
    subs = sorted({s for s, _ in mats})
    vec = {k: np.log1p(upper(v)) for k, v in mats.items()}
    within, between = [], []
    for i, a in enumerate(subs):
        if (a, "test") in vec and (a, "retest") in vec:
            within.append(np.corrcoef(vec[(a, "test")], vec[(a, "retest")])[0, 1])
        for b in subs[i + 1:]:
            for sa in BRAIN_SESSIONS:
                for sb in BRAIN_SESSIONS:
                    if (a, sa) in vec and (b, sb) in vec:
                        between.append(np.corrcoef(vec[(a, sa)], vec[(b, sb)])[0, 1])
    # identification: does each test scan correlate best with its own retest?
    hits = total = 0
    for a in subs:
        if (a, "test") not in vec:
            continue
        scores = {b: np.corrcoef(vec[(a, "test")], vec[(b, "retest")])[0, 1]
                  for b in subs if (b, "retest") in vec}
        if scores:
            total += 1
            hits += max(scores, key=scores.get) == a
    return {"within_subject_r": float(np.mean(within)) if within else None,
            "between_subject_r": float(np.mean(between)) if between else None,
            "n_within": len(within), "n_between": len(between),
            "identification": f"{hits}/{total}"}


def normalise(mats: np.ndarray, totals, target: float | None = None) -> tuple[np.ndarray, float]:
    """Rescale each scan to the same total streamline count (default: the
    median across scans). Tractography yield differs ~2x between scans of
    the same protocol, and an absolute ">= N streamlines" rule would
    otherwise favour the high-yield scans."""
    totals = np.asarray(totals, float)
    target = float(np.median(totals)) if target is None else float(target)
    return mats * (target / totals)[:, None, None], target


QC_MIN_DICE = 0.85
QC_MIN_STREAMLINES = 10_000


def passes_qc(qc: dict) -> bool:
    """Registration and tractography must both have worked. A failed brain
    mask shows up as low Dice and a collapse in streamline count."""
    return qc["registration_dice"] >= QC_MIN_DICE and qc["n_streamlines"] >= QC_MIN_STREAMLINES


def run_group(data_dir, subjects=BRAIN_SUBJECTS, sessions=BRAIN_SESSIONS, *,
              min_streamlines=5, min_fraction=0.5, verbose=True) -> dict:
    mats, qcs, ids = {}, {}, None
    for s, ses in scan_list(subjects, sessions):
        m, qc, scan_ids = run_scan(data_dir, s, ses, verbose=verbose)
        if ids is None:
            ids = scan_ids
        elif scan_ids != ids:
            raise ValueError(f"label table changed between scans (sub-{s} ses-{ses}); clear data/cache")
        mats[(s, ses)], qcs[f"sub-{s}_ses-{ses}"] = m, qc
    excluded = {k: qc for k, qc in qcs.items() if not passes_qc(qc)}
    for k in excluded:
        a, b = k[4:6], k.split("ses-")[1]
        mats.pop((a, b))
        if verbose:
            print(f"[group] excluded {k}: Dice {excluded[k]['registration_dice']:.2f}, "
                  f"{excluded[k]['n_streamlines']} streamlines")
    totals = [qcs[f"sub-{a}_ses-{b}"]["n_streamlines"] for a, b in mats]
    stack, target = normalise(np.stack(list(mats.values())), totals)
    mats = dict(zip(mats, stack))
    weights, consist = consensus(stack, min_streamlines, min_fraction)
    per_scan_edges = [int((upper(m) >= min_streamlines).sum()) for m in stack]
    return {"ids": ids, "weights": weights, "consistency": consist, "stack": stack,
            "keys": list(mats), "qc": qcs, "reliability": reliability(mats),
            "excluded": sorted(excluded),
            "rule": {"min_streamlines": min_streamlines, "min_fraction": min_fraction,
                     "normalised_to_streamlines": round(target)},
            "edges_per_scan": per_scan_edges,
            "edges_consensus": int((upper(weights) > 0).sum())}
