"""Assemble the brain + spinal cord connectome with explicit input/output nodes.

Every edge carries ``provenance``:

* ``dti_brain``   - streamline count from this subject's brain tractography
                    (undirected: stored as u->v and v->u).
* ``pam50_tract`` - spinal tract present at a segment in the PAM50 atlas;
                    weight = tract cross-sectional area (mm^2) there.
* ``anatomy``     - textbook anatomical relay (direction and laterality known
                    from neuroanatomy, not measured here); weight = 1.

Nothing in this graph is neuron-resolution. Nodes are regions, nuclei,
spinal grey-matter zones, and peripheral input/output neuron pools.
"""

from __future__ import annotations

import networkx as nx
import numpy as np

from .sources import SPINAL_LEVELS

OPP = {"L": "R", "R": "L"}


def lv(level: str) -> int:
    return SPINAL_LEVELS.index(level)


def levels_between(a: str, b: str) -> list[str]:
    return SPINAL_LEVELS[lv(a):lv(b) + 1]


def seg(level: str, side: str, zone: str) -> str:
    return f"{level} {side} {zone}"


# (atlas ids L,R) -> tract spec. ``origin``/``target`` name the brain-end node
# with a laterality rule relative to the side of the cord the tract runs in:
# "same" or "opp". ``cord_zones`` are the grey-matter zones it connects to at
# each segment and ``cord_side`` whether that grey matter is on the tract's
# side or crosses at segment level. ``levels`` restricts to the anatomical
# range (intersected with where PAM50 has non-zero area).
TRACTS = [
    # ---- descending (motor / output pathways) ----
    dict(ids=(4, 5), name="lateral corticospinal", dir="down", brain=("Precentral Gyrus", "opp"),
         cord_zones=("ventral horn", "intermediate zone"), cord_side="same", levels=("C1", "S5")),
    dict(ids=(22, 23), name="ventral corticospinal", dir="down", brain=("Precentral Gyrus", "same"),
         cord_zones=("ventral horn",), cord_side="opp", levels=("C1", "T6")),
    dict(ids=(8, 9), name="rubrospinal", dir="down", brain=("Red nucleus", "opp"),
         cord_zones=("intermediate zone", "ventral horn"), cord_side="same", levels=("C1", "C8")),
    dict(ids=(10, 11), name="lateral reticulospinal", dir="down", brain=("Reticular formation", "same"),
         cord_zones=("intermediate zone", "ventral horn"), cord_side="same", levels=("C1", "S5")),
    dict(ids=(16, 17), name="ventrolateral reticulospinal", dir="down", brain=("Reticular formation", "same"),
         cord_zones=("intermediate zone", "ventral horn"), cord_side="same", levels=("C1", "S5")),
    dict(ids=(20, 21), name="ventral reticulospinal", dir="down", brain=("Reticular formation", "same"),
         cord_zones=("ventral horn",), cord_side="same", levels=("C1", "S5")),
    dict(ids=(26, 27), name="medial reticulospinal", dir="down", brain=("Reticular formation", "same"),
         cord_zones=("ventral horn",), cord_side="same", levels=("C1", "S5")),
    dict(ids=(18, 19), name="lateral vestibulospinal", dir="down", brain=("Vestibular nuclei", "same"),
         cord_zones=("ventral horn",), cord_side="same", levels=("C1", "S5")),
    dict(ids=(24, 25), name="tectospinal", dir="down", brain=("Superior colliculus", "opp"),
         cord_zones=("ventral horn",), cord_side="same", levels=("C1", "C4")),
    dict(ids=(28, 29), name="medial longitudinal fasciculus", dir="down", brain=("Vestibular nuclei", "same"),
         cord_zones=("ventral horn",), cord_side="same", levels=("C1", "T1")),
    # ---- ascending (sensory / input pathways) ----
    dict(ids=(0, 1), name="fasciculus gracilis", dir="up", brain=("Gracile nucleus", "same"),
         cord_zones=("DRG",), cord_side="same", levels=("T7", "S5")),
    dict(ids=(2, 3), name="fasciculus cuneatus", dir="up", brain=("Cuneate nucleus", "same"),
         cord_zones=("DRG",), cord_side="same", levels=("C1", "T6")),
    dict(ids=(12, 13), name="spinal lemniscus (spinothalamic)", dir="up", brain=("Thalamus", "same"),
         cord_zones=("dorsal horn",), cord_side="opp", levels=("C1", "S5")),
    dict(ids=(6, 7), name="ventral spinocerebellar", dir="up", brain=("Cerebellum", "opp"),
         cord_zones=("intermediate zone",), cord_side="opp", levels=("T12", "S2")),
    dict(ids=(14, 15), name="spino-olivary", dir="up", brain=("Inferior olive", "same"),
         cord_zones=("dorsal horn",), cord_side="opp", levels=("C1", "S5")),
]

BRAINSTEM_NUCLEI = ["Gracile nucleus", "Cuneate nucleus", "Red nucleus", "Reticular formation",
                    "Vestibular nuclei", "Superior colliculus", "Inferior olive"]

# Directed supraspinal relays (source, target, laterality of target, note).
SUPRASPINAL = [
    ("Gracile nucleus", "Thalamus", "opp", "medial lemniscus (decussates in medulla)"),
    ("Cuneate nucleus", "Thalamus", "opp", "medial lemniscus (decussates in medulla)"),
    ("Thalamus", "Postcentral Gyrus", "same", "VPL -> S1 thalamocortical"),
    ("Thalamus", "Precentral Gyrus", "same", "VL -> M1 thalamocortical"),
    ("Precentral Gyrus", "Brain-Stem", None, "corticospinal/corticobulbar fibres traverse brainstem"),
    ("Precentral Gyrus", "Reticular formation", "same", "corticoreticular"),
    ("Precentral Gyrus", "Red nucleus", "same", "corticorubral"),
    ("Cerebellum", "Red nucleus", "opp", "dentatorubral"),
    ("Cerebellum", "Thalamus", "opp", "dentatothalamic"),
    ("Cerebellum", "Vestibular nuclei", "same", "cerebellovestibular"),
    ("Vestibular nuclei", "Cerebellum", "same", "vestibulocerebellar"),
    ("Inferior olive", "Cerebellum", "opp", "climbing fibres"),
    ("Thalamus", "Reticular formation", "same", "spinoreticular collateral relay"),
]

# Cranial (non-spinal) peripheral inputs/outputs of the brain.
CRANIAL_INPUTS = [
    ("CN I olfactory receptor neurons", "Amygdala", "both", "primary olfactory cortex incl. cortical amygdala"),
    ("CN II retinal ganglion cells", "Thalamus", "both", "optic tract -> LGN (partial decussation at chiasm)"),
    ("CN II retinal ganglion cells", "Superior colliculus", "both", "retinotectal"),
    ("CN V trigeminal ganglion (face touch/pain)", "Brain-Stem", None, "trigeminal nuclei"),
    ("CN VIII cochlear + vestibular ganglia", "Brain-Stem", None, "cochlear nuclei"),
    ("CN VIII cochlear + vestibular ganglia", "Vestibular nuclei", "both", "vestibular nerve"),
    ("CN VII/IX/X taste + visceral afferents", "Brain-Stem", None, "nucleus of the solitary tract"),
]
CRANIAL_OUTPUTS = [
    ("Brain-Stem", "CN III/IV/VI motor neurons (eye muscles)"),
    ("Brain-Stem", "CN V/VII/IX/X/XI/XII motor neurons (jaw, face, pharynx, larynx, neck, tongue)"),
    ("Brain-Stem", "CN III/VII/IX/X parasympathetic preganglionic (pupil, glands, heart, gut)"),
]


def sided(name: str, side: str) -> str:
    return f"{name} ({side})"


def build(brain_labels: list[dict] | None, brain_matrix: np.ndarray | None,
          profiles: dict[int, np.ndarray], min_area_mm2: float = 0.05) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()

    def node(n, **attrs):
        if n not in G:
            G.add_node(n, **attrs)
        return n

    # ---- brain regions (DTI atlas) ----
    if brain_labels is not None:
        for r in brain_labels:
            node(r["id"], system="brain", kind=r["kind"], hemi=r["hemi"], role="interneuron_pool",
                 source="Harvard-Oxford atlas")
        iu = np.triu_indices_from(brain_matrix, 1)
        for i, j in zip(*iu):
            w = float(brain_matrix[i, j])
            if w <= 0:
                continue
            a, b = brain_labels[i]["id"], brain_labels[j]["id"]
            for u, v in ((a, b), (b, a)):
                G.add_edge(u, v, key="dti_brain", provenance="dti_brain", weight=w,
                           directed=False, pathway="white matter (tractography)")
    for side in "LR":
        for nm in BRAINSTEM_NUCLEI:
            node(sided(nm, side), system="brainstem", kind="nucleus", hemi=side,
                 role="relay", parent="Brain-Stem", source="anatomy")
        node(sided("Cerebellum", side), system="brain", kind="cerebellum", hemi=side,
             role="interneuron_pool", source="anatomy")
        for nm in ("Thalamus", "Precentral Gyrus", "Postcentral Gyrus", "Amygdala"):
            node(sided(nm, side), system="brain", kind="subcortex" if nm in ("Thalamus", "Amygdala") else "cortex",
                 hemi=side, role="interneuron_pool", source="Harvard-Oxford atlas")
    node("Brain-Stem", system="brain", kind="brainstem", hemi="M", role="relay",
         source="Harvard-Oxford atlas")

    def anat(u, v, note, **extra):
        G.add_edge(u, v, key="anatomy", provenance="anatomy", weight=1.0, directed=True,
                   pathway=note, **extra)

    for src, dst, lat, note in SUPRASPINAL:
        for side in "LR":
            s = sided(src, side) if src != "Brain-Stem" else src
            if lat is None:
                t = dst
            else:
                t = sided(dst, side if lat == "same" else OPP[side])
            anat(s, t, note)

    # ---- spinal segments + peripheral input/output pools ----
    for li, level in enumerate(SPINAL_LEVELS):
        for side in "LR":
            for zone in ("dorsal horn", "intermediate zone", "ventral horn"):
                node(seg(level, side, zone), system="spinal", kind=zone, hemi=side,
                     level=level, level_index=li, role="interneuron_pool", source="PAM50 grey matter")
    for li, level in enumerate(SPINAL_LEVELS):
        for side in "LR":
            drg = node(seg(level, side, "DRG"), system="peripheral", kind="dorsal root ganglion",
                       hemi=side, level=level, level_index=li, role="input",
                       cell_type="pseudounipolar primary afferent (touch, proprioception, pain, temperature)")
            mot = node(seg(level, side, "motor output"), system="peripheral", kind="ventral root",
                       hemi=side, level=level, level_index=li, role="output",
                       cell_type="alpha/gamma motor neuron axons -> skeletal muscle")
            dh, iz, vh = (seg(level, side, z) for z in ("dorsal horn", "intermediate zone", "ventral horn"))
            anat(drg, dh, "dorsal root entry: all afferents")
            anat(drg, vh, "Ia monosynaptic stretch reflex")
            anat(dh, iz, "segmental interneurons")
            anat(iz, vh, "premotor interneurons")
            anat(iz, seg(level, OPP[side], "ventral horn"), "commissural (crossed extensor reflex)")
            anat(dh, seg(level, OPP[side], "dorsal horn"), "anterior white commissure crossing")
            anat(vh, mot, "ventral root motor efferent")
            if lv("T1") <= li <= lv("L2"):
                sym = node(seg(level, side, "sympathetic output"), system="peripheral",
                           kind="intermediolateral column", hemi=side, level=level, level_index=li,
                           role="output", cell_type="sympathetic preganglionic -> paravertebral ganglia")
                anat(iz, sym, "sympathetic preganglionic outflow")
            if lv("S2") <= li <= lv("S4"):
                para = node(seg(level, side, "parasympathetic output"), system="peripheral",
                            kind="sacral parasympathetic nucleus", hemi=side, level=level, level_index=li,
                            role="output", cell_type="parasympathetic preganglionic -> pelvic viscera")
                anat(iz, para, "sacral parasympathetic outflow")
            if li > 0:
                prev = seg(SPINAL_LEVELS[li - 1], side, "intermediate zone")
                anat(iz, prev, "propriospinal (ascending)")
                anat(prev, iz, "propriospinal (descending)")

    # ---- PAM50 tracts: supraspinal <-> segmental ----
    for t in TRACTS:
        lo, hi = lv(t["levels"][0]), lv(t["levels"][1])
        brain_name, brain_lat = t["brain"]
        for tract_side, atlas_id in zip("LR", t["ids"]):
            area = profiles.get(atlas_id)
            if area is None:
                continue
            bnode = sided(brain_name, tract_side if brain_lat == "same" else OPP[tract_side])
            gm_side = tract_side if t["cord_side"] == "same" else OPP[tract_side]
            for li in range(lo, hi + 1):
                a = float(area[li])
                if a < min_area_mm2:
                    continue
                for zone in t["cord_zones"]:
                    cnode = seg(SPINAL_LEVELS[li], gm_side, zone)
                    u, v = (bnode, cnode) if t["dir"] == "down" else (cnode, bnode)
                    G.add_edge(u, v, key=f"pam50:{t['name']}", provenance="pam50_tract",
                               weight=a, directed=True,
                               pathway=f"{t['name']} tract ({tract_side} cord)",
                               tract_area_mm2=a, atlas_id=atlas_id)

    # ---- cranial peripheral inputs / outputs ----
    for src, dst, lat, note in CRANIAL_INPUTS:
        node(src, system="peripheral", kind="cranial nerve", hemi="M", role="input", cell_type=note)
        targets = [dst] if lat is None else [sided(dst, "L"), sided(dst, "R")]
        for t in targets:
            anat(src, t, note)
    for src, dst in CRANIAL_OUTPUTS:
        node(dst, system="peripheral", kind="cranial nerve", hemi="M", role="output", cell_type=dst)
        anat(src, dst, "cranial nerve efferent")
    return G


def summarize(G: nx.MultiDiGraph) -> dict:
    roles = {}
    for _, d in G.nodes(data=True):
        roles[d.get("role", "?")] = roles.get(d.get("role", "?"), 0) + 1
    prov = {}
    for *_, d in G.edges(data=True):
        prov[d["provenance"]] = prov.get(d["provenance"], 0) + 1
    inputs = [n for n, d in G.nodes(data=True) if d.get("role") == "input"]
    outputs = [n for n, d in G.nodes(data=True) if d.get("role") == "output"]
    return {"nodes": G.number_of_nodes(), "edges": G.number_of_edges(), "roles": roles,
            "edges_by_provenance": prov, "n_inputs": len(inputs), "n_outputs": len(outputs)}


def reachability(G: nx.MultiDiGraph, src: str, dst: str) -> list[list[str]]:
    """All shortest directed paths, ignoring undirected DTI edges (which have
    no known direction) so that the answer reflects signal flow."""
    H = nx.DiGraph()
    for u, v, d in G.edges(data=True):
        if d.get("directed"):
            H.add_edge(u, v)
    try:
        return [list(p) for p in nx.all_shortest_paths(H, src, dst)]
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
