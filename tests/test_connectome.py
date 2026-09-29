"""Offline tests for the brain + spinal cord connectome assembly."""

import numpy as np
import pytest

nx = pytest.importorskip("networkx")
nib = pytest.importorskip("nibabel")

from connectome import brain, graph, spinal  # noqa: E402
from connectome.sources import SPINAL_LEVELS, all_files  # noqa: E402


def fake_profiles(value=1.0):
    return {i: np.full(len(SPINAL_LEVELS), value) for i in range(36)}


def fake_brain():
    table = brain.build_label_table()
    M = np.zeros((len(table), len(table)))
    idx = {r["id"]: r["index"] - 1 for r in table}
    a, b = idx["Precentral Gyrus (L)"], idx["Brain-Stem"]
    M[a, b] = M[b, a] = 42
    return table, M


def test_label_table_is_complete_and_unique():
    table = brain.build_label_table()
    assert len(table) == 48 * 2 + 15 + 2 + 16  # + cerebellum L/R + 16 MASSP nuclei
    assert len({r["id"] for r in table}) == len(table)
    assert [r["index"] for r in table] == list(range(1, len(table) + 1))


def test_combine_atlas_splits_hemispheres_by_mni_x():
    aff = np.diag([2.0, 2.0, 2.0, 1.0])
    aff[0, 3] = -4.0  # voxel x=0,1 -> mm -4,-2 (left); x=2,3 -> 0,2 (right)
    cort = np.zeros((4, 1, 1), np.uint8)
    cort[:, 0, 0] = 7  # precentral everywhere
    sub = np.zeros((4, 1, 1), np.uint8)
    sub[3, 0, 0] = 8  # brainstem overrides on one voxel
    table = brain.build_label_table()
    out = brain.combine_mni_atlas(nib.Nifti1Image(cort, aff), nib.Nifti1Image(sub, aff), table)
    ids = {r["index"]: r["id"] for r in table}
    assert [ids[v] for v in out[:, 0, 0]] == [
        "Precentral Gyrus (L)", "Precentral Gyrus (L)", "Precentral Gyrus (R)", "Brain-Stem"]


def test_grow_labels_fills_only_unlabeled_masked_voxels():
    lab = np.zeros((5, 5, 5), np.int32)
    lab[2, 2, 2] = 3
    mask = np.ones_like(lab, bool)
    mask[2, 2, 3] = False
    out = brain.grow_labels(lab, mask, iterations=1)
    assert out[2, 2, 1] == 3 and out[2, 2, 3] == 0 and out[0, 0, 0] == 0


def test_graph_roles_and_provenance():
    table, M = fake_brain()
    G = graph.build(table, M, fake_profiles())
    s = graph.summarize(G)
    assert set(s["roles"]) == {"input", "output", "relay", "interneuron_pool"}
    # 30 levels x 2 sides of DRGs + cranial afferents
    assert s["n_inputs"] == 60 + len({c[0] for c in graph.CRANIAL_INPUTS})
    assert s["edges_by_provenance"]["dti_brain"] == 2  # undirected -> both directions
    assert all("role" in d for _, d in G.nodes(data=True))


def test_crossing_rules_match_neuroanatomy():
    G = graph.build(None, None, fake_profiles())
    # lateral CST: right motor cortex drives the LEFT cord (pyramidal decussation)
    assert G.has_edge("Precentral Gyrus (R)", "C6 L ventral horn")
    assert not any(d["pathway"].startswith("lateral corticospinal")
                   for d in G.get_edge_data("Precentral Gyrus (R)", "C6 R ventral horn", default={}).values())
    # dorsal columns ascend ipsilaterally; spinothalamic crosses at the segment
    paths = graph.reachability(G, "C7 R DRG", "Thalamus (L)")
    assert ["C7 R DRG", "Cuneate nucleus (R)", "Thalamus (L)"] in paths
    assert ["C7 R DRG", "C7 R dorsal horn", "Thalamus (L)"] in paths
    # gracilis carries only the lower body, cuneatus only the upper body
    assert not G.has_edge("C5 L DRG", "Gracile nucleus (L)")
    assert not G.has_edge("L3 L DRG", "Cuneate nucleus (L)")
    # autonomic outflow only where it exists
    assert "T5 L sympathetic output" in G and "C5 L sympathetic output" not in G
    assert "S3 R parasympathetic output" in G and "L4 R parasympathetic output" not in G


def test_tract_edges_follow_atlas_presence():
    prof = fake_profiles()
    prof[4] = np.zeros(len(SPINAL_LEVELS))  # left lateral CST absent everywhere
    G = graph.build(None, None, prof)
    lcst = [(u, v) for u, v, d in G.edges(data=True)
            if d["provenance"] == "pam50_tract" and d["pathway"] == "lateral corticospinal tract (L cord)"]
    assert lcst == []
    w = [d["weight"] for *_, d in G.edges(data=True) if d["provenance"] == "pam50_tract"]
    assert w and all(x == 1.0 for x in w)


def test_read_atlas_names_skips_combined_and_csf(tmp_path):
    f = tmp_path / "info_label.txt"
    f.write_text("# ID, name, file\n0, WM left fasciculus gracilis, a.nii.gz\n"
                 "36, CSF contour, b.nii.gz\n# Keyword=CombinedLabels\n50, spinal cord, 0:35\n")
    assert spinal.read_atlas_names(f) == {0: "WM left fasciculus gracilis"}


def test_sources_are_pinned():
    files = all_files()
    assert len(files) == 3 + 6 + 3 + 38
    for url in files.values():
        assert url.startswith("https://")
        if "githubusercontent" in url:
            assert "/master/" not in url and "/main/" not in url


def test_environment_records_library_versions():
    from connectome.pipeline import ENV_PACKAGES, environment

    env = environment()
    assert env["python"] and env["platform"]
    assert set(ENV_PACKAGES) <= set(env)
    assert env["numpy"] == np.__version__


def test_scene_point_packing_roundtrip_and_range():
    from connectome import scene

    pts = np.array([[1.23, -45.6, -561.0], [0.0, 0.05, 99.99]])
    back = scene.unpack_points(scene.pack_points(pts, offset=(1.0, 0.0, 0.0)))
    assert np.allclose(back, pts + [1.0, 0, 0], atol=0.051)
    with pytest.raises(ValueError):
        scene.pack_points(np.array([[4000.0, 0, 0]]))


def test_scene_decimate_resamples_by_arc_length():
    from connectome import scene

    line = np.stack([np.zeros(101), np.zeros(101), np.linspace(0, 30, 101)], -1)
    (out,) = scene.decimate([line], n=10, rng=np.random.default_rng(0), spacing_mm=3.0)
    assert len(out) == 11
    assert np.allclose(np.diff(out[:, 2]), 3.0)
    assert np.allclose(out[[0, -1]], line[[0, -1]])


def test_scene_surface_points_are_mask_boundary():
    from connectome import scene

    m = np.zeros((9, 9, 9), bool)
    m[2:7, 2:7, 2:7] = True
    pts = scene.surface_points(m, np.eye(4), n=10_000, rng=np.random.default_rng(0))
    assert len(pts) == 5 ** 3 - 3 ** 3  # shell only, interior excluded
    assert ((pts == 2) | (pts == 6)).any(axis=1).all()


def test_scene_write_html_embeds_payload(tmp_path):
    from connectome import scene

    tpl = scene.VIEWER_TEMPLATE.read_text(encoding="utf-8")
    assert scene.PLACEHOLDER in tpl
    assert "cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js" in tpl
    out = scene.write_html({"nodes": [{"id": "</script><b>"}]}, tmp_path / "v.html")
    html = out.read_text(encoding="utf-8")
    assert scene.PLACEHOLDER not in html
    assert "</script><b>" not in html  # payload cannot close the script tag


def test_group_consensus_keeps_consistent_edges_only():
    from connectome.group import consensus

    mats = np.zeros((4, 3, 3))
    mats[:, 0, 1] = mats[:, 1, 0] = [10, 12, 0, 8]    # 3/4 scans >= 5 -> kept, median 10
    mats[:, 0, 2] = mats[:, 2, 0] = [20, 1, 2, 0]     # 1/4 scans -> dropped
    w, c = consensus(mats, min_streamlines=5, min_fraction=0.5)
    assert w[0, 1] == 10 and c[0, 1] == 0.75
    assert w[0, 2] == 0 and c[0, 2] == 0
    assert np.allclose(w, w.T) and np.all(np.diag(w) == 0)


def test_group_reliability_identifies_subjects():
    from connectome.group import reliability

    rng = np.random.default_rng(0)
    mats = {}
    for s in ("01", "02", "03"):
        base = rng.gamma(2, 10, (20, 20))
        base = base + base.T
        for ses in ("test", "retest"):
            noisy = base * rng.uniform(0.9, 1.1, base.shape)
            mats[(s, ses)] = (noisy + noisy.T) / 2
    r = reliability(mats)
    assert r["within_subject_r"] > r["between_subject_r"]
    assert r["identification"] == "3/3"


def test_combine_atlas_layers_massp_over_brainstem():
    aff = np.eye(4)
    cort = np.zeros((4, 1, 1), np.uint8)
    sub = np.full((4, 1, 1), 8, np.uint8)          # all Harvard-Oxford brainstem
    massp = np.zeros((4, 1, 1), np.uint8)
    massp[1, 0, 0] = 7                               # left red nucleus
    table = brain.build_label_table()
    out = brain.combine_mni_atlas(nib.Nifti1Image(cort, aff), nib.Nifti1Image(sub, aff), table,
                                  massp=nib.Nifti1Image(massp, aff))
    ids = {r["index"]: r["id"] for r in table}
    assert [ids[v] for v in out[:, 0, 0]] == ["Brain-Stem", "Red nucleus (L)", "Brain-Stem", "Brain-Stem"]


def test_atlas_nuclei_replace_schematic_relays():
    table, M = fake_brain()
    G = graph.build(table, M, fake_profiles())
    assert G.nodes["Red nucleus (L)"]["source"] == "MASSP atlas"
    assert G.nodes["Red nucleus (L)"]["role"] == "relay"
    assert G.nodes["Cerebellum (R)"]["source"] == "aseg atlas"
    assert G.has_edge("Inferior colliculus (L)", "Thalamus (L)")
    # without brain labels the MASSP-only relays are skipped, not left dangling
    G0 = graph.build(None, None, fake_profiles())
    assert "Inferior colliculus (L)" not in G0
