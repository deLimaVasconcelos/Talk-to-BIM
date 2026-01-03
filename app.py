# app.py
# -*- coding: utf-8 -*-

import hashlib
import tempfile
from collections import defaultdict
from typing import Dict, List, Tuple

import streamlit as st
import numpy as np
import plotly.graph_objects as go

# ------------------------------------------------------------
# IFC / Geometrie
# ------------------------------------------------------------
try:
    import ifcopenshell  # type: ignore
    IFC_OK = True
except Exception:
    IFC_OK = False
    ifcopenshell = None  # type: ignore

try:
    if IFC_OK:
        import ifcopenshell.geom as ifcgeom  # type: ignore
        IFC_GEOM_OK = True
    else:
        IFC_GEOM_OK = False
        ifcgeom = None  # type: ignore
except Exception:
    IFC_GEOM_OK = False
    ifcgeom = None  # type: ignore


# ------------------------------------------------------------
# Konfiguration: Community-Cloud-sicher
# ------------------------------------------------------------
# Hartes Render-Limit (ohne UI) – verhindert Memory-Lockups
MAX_RENDER_TOTAL = 350           # konservativ: 250–450
PER_CLASS_CAP = 120              # pro Klasse
OPACITY = 0.22

# Sinnvolle GA/MEP-Defaults (zeigt "alles Relevante" statt "alles IfcProduct")
MEP_DEFAULT = [
    "IfcPipeSegment", "IfcPipeFitting",
    "IfcDuctSegment", "IfcDuctFitting",
    "IfcValve", "IfcPump",
    "IfcSensor", "IfcActuator",
    "IfcFlowInstrument", "IfcFlowMeter",
    "IfcDamper", "IfcFilter",
    "IfcCoil", "IfcTank", "IfcHeatExchanger", "IfcFan",
    "IfcElectricMotor", "IfcCompressor",
    "IfcDistributionControlElement",
]


# ------------------------------------------------------------
# Streamlit Page
# ------------------------------------------------------------
st.set_page_config(page_title="Talk2BIM – IFC Viewer", page_icon="🧩", layout="wide")
st.title("Talk2BIM – IFC Viewer")

if not IFC_OK:
    st.error("IfcOpenShell ist nicht verfügbar. Bitte `ifcopenshell` in requirements.txt aufnehmen.")
    st.stop()

if not IFC_GEOM_OK:
    st.error(
        "Das Geometrie-Modul `ifcopenshell.geom` ist nicht verfügbar. "
        "In Streamlit Cloud muss `ifcopenshell` mit Geometrie-Unterstützung verfügbar sein."
    )
    st.stop()


# ------------------------------------------------------------
# Utilities / Cache
# ------------------------------------------------------------
def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


@st.cache_data(show_spinner=False)
def write_tmp_ifc(file_bytes: bytes) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ifc")
    tmp.write(file_bytes)
    tmp.close()
    return tmp.name


@st.cache_resource(show_spinner=False)
def open_ifc(path: str):
    return ifcopenshell.open(path)


@st.cache_data(show_spinner=False)
def collect_class_counts_renderable(ifc_path: str) -> Dict[str, int]:
    """
    Zählt nur IfcProduct mit Representation (renderbar).
    """
    ifc = ifcopenshell.open(ifc_path)
    counts: Dict[str, int] = defaultdict(int)
    try:
        prods = ifc.by_type("IfcProduct") or []
    except Exception:
        prods = []
    for e in prods:
        try:
            if getattr(e, "Representation", None) is None:
                continue
            counts[e.is_a()] += 1
        except Exception:
            continue
    return dict(counts)


def make_settings():
    s = ifcgeom.settings()

    def set_if_exists(attr: str, value):
        key = getattr(s, attr, None)
        if key is not None:
            s.set(key, value)

    set_if_exists("USE_WORLD_COORDS", True)
    set_if_exists("WELD_VERTICES", True)
    set_if_exists("APPLY_DEFAULT_MATERIALS", True)
    set_if_exists("SEW_SHELLS", True)
    return s


def dummy_box(note: str) -> go.Figure:
    fig = go.Figure()
    x = [0, 5, 5, 0, 0, 5, 5, 0]
    y = [0, 0, 4, 4, 0, 0, 4, 4]
    z = [0, 0, 0, 0, 3, 3, 3, 3]
    I = [0, 0, 4, 4, 0, 0, 3, 3, 0, 0, 1, 1]
    J = [1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 2, 6]
    K = [2, 3, 6, 7, 5, 4, 6, 7, 7, 4, 6, 5]
    fig.add_trace(go.Mesh3d(x=x, y=y, z=z, i=I, j=J, k=K, opacity=0.25, flatshading=True, showscale=False))
    fig.add_annotation(text=note, showarrow=False)
    fig.update_layout(height=650, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)
    return fig


def choose_even_indices(n: int, k: int) -> np.ndarray:
    if n <= 0 or k <= 0:
        return np.array([], dtype=int)
    k = min(k, n)
    if k == n:
        return np.arange(n, dtype=int)
    return np.linspace(0, n - 1, k, dtype=int)


def render_ifc_classes_sampled(ifc, classes: List[str], per_class_cap: int, total_cap: int) -> Tuple[go.Figure, Dict[str, int]]:
    """
    Render: pro Klasse cap + Sampling, Gesamtcap.
    Stabil für Cloud-Limits.
    """
    settings = make_settings()
    fig = go.Figure()

    mins = np.array([+1e9, +1e9, +1e9], dtype=float)
    maxs = np.array([-1e9, -1e9, -1e9], dtype=float)

    attempted = 0
    rendered = 0

    prog = st.progress(0, text="Geometrie wird aufgebaut …")
    cls_total = max(1, len(classes))
    cls_done = 0

    for cls in classes:
        if rendered >= total_cap:
            break

        try:
            elems = ifc.by_type(cls) or []
        except Exception:
            elems = []

        # nur renderbare
        elems = [e for e in elems if getattr(e, "Representation", None) is not None]
        if not elems:
            cls_done += 1
            prog.progress(min(1.0, cls_done / cls_total), text=f"Gerendert: {rendered}")
            continue

        # Sampling innerhalb der Klasse
        take = min(per_class_cap, len(elems), max(1, total_cap - rendered))
        idxs = choose_even_indices(len(elems), take)

        for idx in idxs:
            if rendered >= total_cap:
                break

            e = elems[int(idx)]
            attempted += 1
            try:
                shape = ifcgeom.create_shape(settings, e)
                verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
                faces = np.array(shape.geometry.faces, dtype=int).reshape(-1, 3)
                if verts.size == 0 or faces.size == 0:
                    continue

                mins = np.minimum(mins, verts.min(axis=0))
                maxs = np.maximum(maxs, verts.max(axis=0))

                fig.add_trace(go.Mesh3d(
                    x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
                    i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                    opacity=OPACITY, flatshading=True, showscale=False
                ))
                rendered += 1
            except Exception:
                continue

        cls_done += 1
        prog.progress(min(1.0, cls_done / cls_total), text=f"Gerendert: {rendered} (geprüft: {attempted})")

    prog.progress(1.0, text=f"Fertig. Gerendert: {rendered} (geprüft: {attempted})")

    if rendered == 0:
        return dummy_box("Keine Geometrie gerendert. (Representation fehlt oder create_shape scheitert.)"), {
            "attempted": attempted, "rendered": rendered
        }

    if np.all(np.isfinite(mins)) and np.all(np.isfinite(maxs)):
        L = maxs - mins
        L[L <= 0] = 1.0
        pad = 0.06
        fig.update_layout(
            scene=dict(
                xaxis=dict(title="x", range=[mins[0] - pad * L[0], maxs[0] + pad * L[0]]),
                yaxis=dict(title="y", range=[mins[1] - pad * L[1], maxs[1] + pad * L[1]]),
                zaxis=dict(title="z", range=[mins[2] - pad * L[2], maxs[2] + pad * L[2]]),
                aspectmode="data",
            ),
            margin=dict(l=0, r=0, t=10, b=0),
            height=650,
            showlegend=False
        )
    else:
        fig.update_layout(height=650, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)

    return fig, {"attempted": attempted, "rendered": rendered}


# ------------------------------------------------------------
# UI (nur Upload + Viewer; keine Bot-UI)
# ------------------------------------------------------------
with st.sidebar:
    st.header("IFC hochladen")
    uploaded = st.file_uploader("Datei auswählen", type=["ifc", "IFC"])

    st.write("---")
    st.caption("Standard: GA/MEP-Klassen (performant). Optional kann man Klassen abwählen.")

if uploaded is None:
    st.info("Bitte eine IFC-Datei hochladen.")
    st.stop()

file_bytes = uploaded.getvalue()
if not file_bytes:
    st.warning("Upload ist leer. Bitte erneut hochladen.")
    st.stop()

h = sha1(file_bytes)

# Session stabilisieren
if "ifc_hash" not in st.session_state or st.session_state.ifc_hash != h:
    st.session_state.ifc_hash = h
    st.session_state.ifc_path = write_tmp_ifc(file_bytes)
    st.session_state.class_counts = None
    st.session_state.selected_classes = None

# IFC öffnen
with st.spinner("IFC wird geöffnet …"):
    ifc = open_ifc(st.session_state.ifc_path)

# Klassen zählen (renderbar)
if st.session_state.class_counts is None:
    with st.spinner("Klassen werden ermittelt …"):
        st.session_state.class_counts = collect_class_counts_renderable(st.session_state.ifc_path)

counts: Dict[str, int] = st.session_state.class_counts or {}
available = sorted(counts.keys(), key=lambda k: counts.get(k, 0), reverse=True)

# Default: MEP_DEFAULT ∩ verfügbare Klassen; wenn leer, dann alle
default_classes = [c for c in MEP_DEFAULT if c in counts and counts.get(c, 0) > 0]
if not default_classes:
    default_classes = available

# Optionaler Filter (nur Klassen)
with st.sidebar:
    with st.expander("Klassen (optional)"):
        if not available:
            st.warning("Keine renderbaren Klassen gefunden.")
        else:
            sel = st.multiselect(
                "Anzeige-Klassen",
                options=available,
                default=default_classes,
                help="Für Community Cloud ist das Rendern ALLER Produkte oft zu speicherintensiv. "
                     "MEP-Defaults sind meist ausreichend."
            )
            st.session_state.selected_classes = sel

selected_classes = st.session_state.selected_classes or default_classes

st.subheader("3D-Viewer")

# Render (cloud-sicher)
fig, stats = render_ifc_classes_sampled(
    ifc,
    classes=selected_classes,
    per_class_cap=PER_CLASS_CAP,
    total_cap=MAX_RENDER_TOTAL
)

st.plotly_chart(fig, use_container_width=True)

st.caption(
    f"Gerendert (Stichprobe): {stats.get('rendered', 0)} Elemente | "
    f"Geprüft: {stats.get('attempted', 0)} | "
    f"Limits: total={MAX_RENDER_TOTAL}, pro Klasse={PER_CLASS_CAP}"
)
