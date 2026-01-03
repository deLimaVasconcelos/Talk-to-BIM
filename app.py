# app.py
# -*- coding: utf-8 -*-

import os
import tempfile
from typing import List, Tuple, Optional

import streamlit as st
import numpy as np
import plotly.graph_objects as go

# ------------------------------------------------------------
# IFC / Geometrie (robust)
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
        "In Streamlit Cloud muss `ifcopenshell` so installiert sein, dass OCC/Geom unterstützt wird."
    )
    st.stop()


# ------------------------------------------------------------
# Performance: Cache IFC-Öffnen + Index
# ------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _write_bytes_to_tmp(file_bytes: bytes, suffix: str = ".ifc") -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(file_bytes)
    tmp.close()
    return tmp.name


@st.cache_resource(show_spinner=False)
def _open_ifc_from_path(path: str):
    return ifcopenshell.open(path)


def _make_geom_settings():
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


def _dummy_box(note: str) -> go.Figure:
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


def _iter_candidate_products(ifc) -> List:
    """
    Performance-orientiert: Nur IfcProduct mit Representation.
    (Viele Modelle enthalten sehr viele IfcProduct ohne renderbare Repräsentation.)
    """
    try:
        prods = ifc.by_type("IfcProduct") or []
    except Exception:
        return []
    out = []
    for e in prods:
        if getattr(e, "Representation", None) is not None:
            out.append(e)
    return out


@st.cache_data(show_spinner=False)
def _choose_sample_indices(n: int, max_items: int) -> np.ndarray:
    """
    Wählt gleichmäßig verteilte Indizes (Sampling), damit große Modelle schnell etwas zeigen.
    """
    if n <= 0:
        return np.array([], dtype=int)
    k = min(max_items, n)
    if k == n:
        return np.arange(n, dtype=int)
    return np.linspace(0, n - 1, k, dtype=int)


def render_ifc_view(ifc, max_items: int = 250) -> Tuple[go.Figure, dict]:
    """
    Rendert eine performante Vorschau:
    - Kandidaten: IfcProduct mit Representation
    - Sampling auf max_items
    - pro Element create_shape; fehlerhafte Elemente werden übersprungen
    """
    settings = _make_geom_settings()
    candidates = _iter_candidate_products(ifc)

    if not candidates:
        return _dummy_box("Keine renderbaren IfcProduct-Repräsentationen gefunden."), {
            "candidates": 0,
            "attempted": 0,
            "rendered": 0,
        }

    sel_idx = _choose_sample_indices(len(candidates), max_items)
    fig = go.Figure()

    mins = np.array([+1e9, +1e9, +1e9], dtype=float)
    maxs = np.array([-1e9, -1e9, -1e9], dtype=float)

    attempted = 0
    rendered = 0

    prog = st.progress(0, text="Geometrie wird aufgebaut …")
    total = max(1, len(sel_idx))

    for t, idx in enumerate(sel_idx, start=1):
        e = candidates[int(idx)]
        attempted += 1
        try:
            shape = ifcgeom.create_shape(settings, e)
            verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
            faces = np.array(shape.geometry.faces, dtype=int).reshape(-1, 3)

            if verts.size == 0 or faces.size == 0:
                continue

            mins = np.minimum(mins, verts.min(axis=0))
            maxs = np.maximum(maxs, verts.max(axis=0))

            fig.add_trace(
                go.Mesh3d(
                    x=verts[:, 0],
                    y=verts[:, 1],
                    z=verts[:, 2],
                    i=faces[:, 0],
                    j=faces[:, 1],
                    k=faces[:, 2],
                    opacity=0.22,
                    flatshading=True,
                    showscale=False,
                )
            )
            rendered += 1
        except Exception:
            # einzelne Elemente können scheitern; wir bleiben robust
            pass

        if t % 10 == 0 or t == total:
            prog.progress(min(1.0, t / total), text=f"Gerendert: {rendered} / {t} geprüft")

    prog.progress(1.0, text=f"Fertig. Gerendert: {rendered} / {attempted} geprüft")

    if rendered == 0:
        return _dummy_box(
            "Es wurden Elemente geprüft, aber keine Geometrie konnte erzeugt werden. "
            "Das Modell kann dennoch Daten enthalten."
        ), {
            "candidates": len(candidates),
            "attempted": attempted,
            "rendered": rendered,
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
            showlegend=False,
        )
    else:
        fig.update_layout(height=650, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)

    return fig, {"candidates": len(candidates), "attempted": attempted, "rendered": rendered}


# ------------------------------------------------------------
# UI – nur Upload + Viewer (performant)
# ------------------------------------------------------------
with st.sidebar:
    st.header("IFC laden")
    uploaded = st.file_uploader("IFC hochladen", type=["ifc", "IFC"])
    st.write("---")
    st.subheader("Performance")
    max_items = st.slider(
        "Vorschau: max. Anzahl gerenderter Elemente",
        min_value=50,
        max_value=800,
        value=250,
        step=50,
        help="Für große Modelle sind 150–300 meist ideal. Höhere Werte können deutlich länger dauern.",
    )
    render_now = st.button("3D anzeigen", type="primary")

if uploaded is None:
    st.info("Bitte links eine IFC-Datei hochladen und anschließend **„3D anzeigen“** klicken.")
    st.stop()

file_bytes = uploaded.getvalue()
if not file_bytes:
    st.warning("Upload ist leer. Bitte erneut hochladen.")
    st.stop()

# IFC öffnen (gecached)
with st.spinner("IFC-Datei wird vorbereitet …"):
    tmp_path = _write_bytes_to_tmp(file_bytes, suffix=".ifc")
    ifc = _open_ifc_from_path(tmp_path)

# Erst rendern, wenn Nutzer klickt (wichtig für Performance & UX)
if not render_now:
    st.success("IFC geladen. Klicken Sie links auf **„3D anzeigen“**, um die Vorschau zu rendern.")
    st.caption("Hinweis: Der Viewer rendert eine Stichprobe (Sampling) für schnelle Rückmeldung.")
    st.stop()

fig, stats = render_ifc_view(ifc, max_items=max_items)
st.subheader("3D-Viewer")
st.plotly_chart(fig, use_container_width=True)
st.caption(
    f"Kandidaten (IfcProduct mit Representation): {stats['candidates']} | "
    f"Geprüft: {stats['attempted']} | Gerendert: {stats['rendered']} | "
    f"Vorschau-Limit: {max_items}"
)
