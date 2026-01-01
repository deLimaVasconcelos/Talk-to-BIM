# app.py
# -*- coding: utf-8 -*-

import os
import re
import tempfile
from collections import defaultdict
from typing import Dict, Any, List, Optional, Tuple

import streamlit as st
import numpy as np
import plotly.graph_objects as go

# ------------------------------------------------------------
# IFC Import (robust)
# ------------------------------------------------------------
try:
    import ifcopenshell  # type: ignore
    IFC_OK = True
except Exception:
    IFC_OK = False
    ifcopenshell = None

# Optional: Geometrie (Viewer) – kann in Cloud-Umgebungen fehlen
try:
    if IFC_OK:
        import ifcopenshell.geom as ifcgeom  # type: ignore
        IFC_GEOM_OK = True
    else:
        IFC_GEOM_OK = False
except Exception:
    IFC_GEOM_OK = False
    ifcgeom = None

# ------------------------------------------------------------
# Viewer-Klassen (MEP/GA)
# ------------------------------------------------------------
MEP_VIEW_CLASSES = [
    "IfcPipeSegment", "IfcPipeFitting",
    "IfcDuctSegment", "IfcDuctFitting",
    "IfcPump", "IfcValve",
    "IfcFlowInstrument", "IfcFlowController",
    "IfcSensor", "IfcActuator",
    "IfcFan", "IfcCoil",
    "IfcTank", "IfcHeatExchanger",
    "IfcUnitaryEquipment", "IfcUnitaryControlElement",
    "IfcBuildingElementProxy", "IfcCovering",
]

# Optional: einfache Kategorien (für Zusammenfassung/Filter)
GA_CLASSES: Dict[str, set] = {
    "Lüftung": {"IfcDuctSegment", "IfcDuctFitting", "IfcFan", "IfcAirTerminal", "IfcDamper"},
    "Heizen": {"IfcRadiator", "IfcBoiler", "IfcPipeSegment", "IfcHeatExchanger", "IfcBuildingElementProxy"},
    "Kühlen": {"IfcChiller", "IfcCoolingTower", "IfcCoil", "IfcCovering"},
    "Licht": {"IfcLightFixture", "IfcLamp"},
    "Steuerung": {"IfcSensor", "IfcActuator", "IfcController", "IfcAlarm", "IfcUnitaryControlElement"},
}

# ------------------------------------------------------------
# Hilfsfunktionen (Text/Properties)
# ------------------------------------------------------------
def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def safe_str(x) -> str:
    try:
        return str(x) if x is not None else ""
    except Exception:
        return ""

def get_psets(element) -> Dict[str, Dict[str, str]]:
    """
    Liest einfache PropertySets:
      IfcRelDefinesByProperties -> IfcPropertySet -> IfcPropertySingleValue
    """
    out: Dict[str, Dict[str, str]] = {}
    rels = getattr(element, "IsDefinedBy", None) or []
    for rel in rels:
        try:
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            pdef = rel.RelatingPropertyDefinition
            if not pdef or not pdef.is_a("IfcPropertySet"):
                continue
            props: Dict[str, str] = {}
            for p in pdef.HasProperties or []:
                if p.is_a("IfcPropertySingleValue"):
                    name = safe_str(getattr(p, "Name", ""))
                    nv = getattr(p, "NominalValue", None)
                    props[name] = safe_str(getattr(nv, "wrappedValue", nv))
            out[safe_str(getattr(pdef, "Name", "Pset"))] = props
        except Exception:
            continue
    return out

def classify_category(ifc_class: str) -> Optional[str]:
    for cat, clsset in GA_CLASSES.items():
        if ifc_class in clsset:
            return cat
    return None

# ------------------------------------------------------------
# Modellindex (klassenbasiert; Räume optional)
# ------------------------------------------------------------
def count_type(ifc, tname: str) -> int:
    try:
        return len(ifc.by_type(tname) or [])
    except Exception:
        return 0

def build_global_index(ifc) -> Dict[str, Any]:
    """
    Erzeugt einen globalen Index über IfcProduct-Objekte:
      - Klassenliste
      - Zählungen
      - Lookup nach GlobalId (Meta + Psets on demand)
    """
    class_counts: Dict[str, int] = defaultdict(int)
    by_class_ids: Dict[str, List[str]] = defaultdict(list)
    lookup: Dict[str, Dict[str, Any]] = {}

    try:
        products = ifc.by_type("IfcProduct") or []
    except Exception:
        products = []

    for e in products:
        try:
            gid = safe_str(getattr(e, "GlobalId", ""))
            if not gid:
                continue
            cls = e.is_a()
            class_counts[cls] += 1
            by_class_ids[cls].append(gid)
            lookup[gid] = {
                "global_id": gid,
                "ifc_class": cls,
                "name": safe_str(getattr(e, "Name", "")),
                "object_type": safe_str(getattr(e, "ObjectType", "")),
                "predefined_type": safe_str(getattr(e, "PredefinedType", "")),
                "category": classify_category(cls) or "",
            }
        except Exception:
            continue

    return {
        "class_counts": dict(class_counts),
        "by_class_ids": dict(by_class_ids),
        "lookup": lookup,
    }

def format_item_line(meta: Dict[str, Any]) -> str:
    cat = meta.get("category", "")
    cat_part = f"[{cat}] " if cat else ""
    return f"- {cat_part}{meta.get('ifc_class','')} | {meta.get('name','')} | {meta.get('global_id','')}"

# ------------------------------------------------------------
# 3D-Viewer (Plotly Mesh3d)
# ------------------------------------------------------------
def _dummy_box_figure(note: str) -> go.Figure:
    fig = go.Figure()
    x = [0, 5, 5, 0, 0, 5, 5, 0]
    y = [0, 0, 4, 4, 0, 0, 4, 4]
    z = [0, 0, 0, 0, 3, 3, 3, 3]
    I = [0, 0, 4, 4, 0, 0, 3, 3, 0, 0, 1, 1]
    J = [1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 2, 6]
    K = [2, 3, 6, 7, 5, 4, 6, 7, 7, 4, 6, 5]
    fig.add_trace(go.Mesh3d(x=x, y=y, z=z, i=I, j=J, k=K, opacity=0.35, flatshading=True, showscale=False))
    fig.add_annotation(text=note, showarrow=False)
    fig.update_layout(height=560, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)
    return fig

def plot_ifc_mesh_by_classes(ifc, classes: Tuple[str, ...]) -> go.Figure:
    if not IFC_GEOM_OK:
        return _dummy_box_figure("Geometrie-Backend (ifcopenshell.geom) nicht verfügbar → Dummy-Viewer")

    settings = ifcgeom.settings()

    def set_if_exists(attr: str, value):
        key = getattr(settings, attr, None)
        if key is not None:
            settings.set(key, value)

    set_if_exists("USE_WORLD_COORDS", True)
    set_if_exists("WELD_VERTICES", True)
    set_if_exists("APPLY_DEFAULT_MATERIALS", True)
    set_if_exists("SEW_SHELLS", True)

    fig = go.Figure()
    mins = np.array([+1e9, +1e9, +1e9], dtype=float)
    maxs = np.array([-1e9, -1e9, -1e9], dtype=float)

    # Schutzlimit (Cloud/Browser)
    max_elems_per_class = 800

    added = 0
    for cls in classes:
        try:
            elems = ifc.by_type(cls) or []
        except Exception:
            elems = []
        for e in elems[:max_elems_per_class]:
            try:
                shape = ifcgeom.create_shape(settings, e)
                verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
                faces = np.array(shape.geometry.faces, dtype=int).reshape(-1, 3)

                mins = np.minimum(mins, verts.min(axis=0))
                maxs = np.maximum(maxs, verts.max(axis=0))

                fig.add_trace(go.Mesh3d(
                    x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
                    i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                    opacity=0.22,
                    flatshading=True,
                    name=cls,
                    showscale=False
                ))
                added += 1
            except Exception:
                continue

    # Wenn gar nichts gezeichnet wurde: Hinweis + Dummy
    if added == 0:
        return _dummy_box_figure("Keine Geometrie für die gewählten Klassen erzeugt → prüfen Sie die Klassenauswahl")

    # Achsen/Range
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
            height=560,
            showlegend=False
        )
    else:
        fig.update_layout(height=560, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)

    return fig

# ------------------------------------------------------------
# Chat-Engine (deterministisch, ohne externe KI)
# ------------------------------------------------------------
def chat_help() -> str:
    return (
        "**Mögliche Abfragen:**\n"
        "- `hilfe`\n"
        "- `liste klassen`\n"
        "- `anzahl IfcPipeSegment` (oder jede andere IFC-Klasse)\n"
        "- `liste IfcPump` (zeigt erste Elemente dieser Klasse)\n"
        "- `details id <GlobalId>`\n"
        "- `psets id <GlobalId>`\n"
        "- `suche \"text\"` (suche in Name/ObjectType/PredefinedType/Klasse)\n"
    )

def answer(question: str, ifc, gindex: Dict[str, Any]) -> str:
    q = normalize(question)

    if q in {"hilfe", "help", "?"}:
        return chat_help()

    if q in {"liste klassen", "klassen", "ifc klassen"}:
        cc = gindex.get("class_counts", {})
        if not cc:
            return "Keine IfcProduct-Objekte gefunden."
        lines = ["**IFC-Klassen im Modell (Top 40):**"]
        top = sorted(cc.items(), key=lambda kv: kv[1], reverse=True)[:40]
        for k, v in top:
            lines.append(f"- {k}: {v}")
        lines.append("\nTipp: `liste <Klasse>` oder `anzahl <Klasse>`.")
        return "\n".join(lines)

    m_cnt = re.search(r"\banzahl\s+([A-Za-z0-9_]+)\b", question)
    if m_cnt:
        cls = m_cnt.group(1)
        n = gindex.get("class_counts", {}).get(cls)
        if n is None:
            return f"Die Klasse `{cls}` kommt im Modell nicht vor (oder wurde nicht als IfcProduct gezählt)."
        return f"**{cls}: {n}**"

    m_list = re.search(r"\bliste\s+([A-Za-z0-9_]+)\b", question)
    if m_list:
        cls = m_list.group(1)
        ids = gindex.get("by_class_ids", {}).get(cls, [])
        if not ids:
            return f"Keine Elemente der Klasse `{cls}` gefunden."
        lines = [f"**{cls} – erste {min(30, len(ids))} Elemente:**"]
        for gid in ids[:30]:
            meta = gindex["lookup"].get(gid, {"global_id": gid, "ifc_class": cls, "name": ""})
            lines.append(format_item_line(meta))
        return "\n".join(lines)

    m_details = re.search(r"\bdetails\s+id\s+([0-9A-Za-z_$]{6,})\b", question, flags=re.IGNORECASE)
    if m_details:
        gid = m_details.group(1)
        meta = gindex.get("lookup", {}).get(gid)
        if not meta:
            return f"Kein Element mit GlobalId `{gid}` gefunden."
        return (
            f"**Details – {gid}:**\n"
            f"- IFC-Klasse: {meta.get('ifc_class','')}\n"
            f"- Name: {meta.get('name','')}\n"
            f"- ObjectType: {meta.get('object_type','')}\n"
            f"- PredefinedType: {meta.get('predefined_type','')}\n"
            f"- Kategorie: {meta.get('category','') or '—'}\n"
        )

    m_psets = re.search(r"\bpsets\s+id\s+([0-9A-Za-z_$]{6,})\b", question, flags=re.IGNORECASE)
    if m_psets:
        gid = m_psets.group(1)
        meta = gindex.get("lookup", {}).get(gid)
        if not meta:
            return f"Kein Element mit GlobalId `{gid}` gefunden."
        # Element im IFC wiederfinden
        try:
            elem = ifc.by_guid(gid)
        except Exception:
            elem = None
        if elem is None:
            return f"Element `{gid}` konnte im IFC nicht aufgelöst werden."
        psets = get_psets(elem) or {}
        if not psets:
            return f"Für `{gid}` wurden keine PropertySets gefunden."
        lines = [f"**PropertySets – {gid}:**"]
        for psn, props in psets.items():
            lines.append(f"- {psn}")
            for k, v in list(props.items())[:35]:
                lines.append(f"  - {k}: {v}")
        return "\n".join(lines)

    m_search = re.search(r'suche\s+"([^"]+)"', question, flags=re.IGNORECASE)
    if m_search:
        needle_raw = m_search.group(1).strip()
        needle = normalize(needle_raw)
        if not needle:
            return "Bitte einen Suchbegriff angeben, z. B. `suche \"pumpe\"`."
        hits = []
        for gid, meta in gindex.get("lookup", {}).items():
            hay = " ".join([
                normalize(meta.get("ifc_class", "")),
                normalize(meta.get("name", "")),
                normalize(meta.get("object_type", "")),
                normalize(meta.get("predefined_type", "")),
                normalize(meta.get("category", "")),
            ])
            if needle in hay:
                hits.append(meta)
        if not hits:
            return f"Keine Treffer für „{needle_raw}“ gefunden."
        lines = [f"**Treffer für „{needle_raw}“ (erste {min(40, len(hits))}):**"]
        for meta in hits[:40]:
            lines.append(format_item_line(meta))
        return "\n".join(lines)

    return "Ich konnte die Anfrage nicht zuordnen. Bitte `hilfe` eingeben."

# ------------------------------------------------------------
# Streamlit UI
# ------------------------------------------------------------
st.set_page_config(page_title="Talk2BIM", page_icon="🗣️", layout="wide")
st.title("Talk2BIM")

if not IFC_OK:
    st.error("IfcOpenShell konnte nicht geladen werden. Bitte requirements.txt prüfen.")
    st.stop()

# Sidebar: IFC laden + Viewer-Klassen + Diagnose
with st.sidebar:
    st.header("IFC laden")

    use_default = st.toggle("Repo-Datei verwenden (default.ifc)", value=True)
    default_path = "default.ifc"

    uploaded = None
    if not use_default:
        uploaded = st.file_uploader("IFC hochladen", type=["ifc", "IFC"])

    if use_default:
        if not os.path.exists(default_path):
            st.warning("default.ifc wurde im Repository nicht gefunden.")
            st.stop()
        ifc_path = default_path
    else:
        if uploaded is None:
            st.info("Bitte eine IFC-Datei auswählen.")
            st.stop()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ifc")
        tmp.write(uploaded.read())
        tmp.close()
        ifc_path = tmp.name

    with st.spinner("IFC wird geöffnet …"):
        ifc = ifcopenshell.open(ifc_path)

    # Globaler Index (für Chat/Counts)
    with st.spinner("Index wird aufgebaut …"):
        gindex = build_global_index(ifc)

    # Diagnose (kurz und nützlich)
    st.success("IFC geladen.")
    st.caption(f"IFC_GEOM_OK (Viewer): {IFC_GEOM_OK}")
    st.caption(f"IfcSpace (Räume): {count_type(ifc, 'IfcSpace')}")

    st.write("---")
    st.subheader("3D-Viewer – Klassen")

    selected_view_classes = st.multiselect(
        "Welche IFC-Klassen sollen angezeigt werden?",
        options=MEP_VIEW_CLASSES,
        default=[
            "IfcPipeSegment",
            "IfcPipeFitting",
            "IfcDuctSegment",
            "IfcDuctFitting",
            "IfcPump",
            "IfcValve",
        ],
    )

    st.write("---")
    st.subheader("Kurzübersicht (MEP)")

    # Zähle nur ausgewählte (und ein paar typische) Klassen, um sofort Feedback zu geben
    quick = ["IfcPipeSegment", "IfcDuctSegment", "IfcPump", "IfcValve", "IfcSensor", "IfcActuator"]
    cc = gindex.get("class_counts", {})
    for t in quick:
        st.write(f"- {t}: {cc.get(t, 0)}")

    st.write("---")
    st.caption("Chat-Beispiele: `hilfe`, `liste klassen`, `liste IfcPump`, `suche \"valve\"`.")

# Hauptbereich: Viewer + Chat
col_view, col_chat = st.columns([6, 5])

with col_view:
    st.subheader("3D-Viewer")
    fig = plot_ifc_mesh_by_classes(ifc, classes=tuple(selected_view_classes))
    st.plotly_chart(fig, use_container_width=True)

with col_chat:
    st.subheader("Chat")

    if "chat" not in st.session_state:
        st.session_state.chat = [
            {"role": "assistant", "content": "Geben Sie `hilfe` ein, um mögliche Abfragen zu sehen."}
        ]

    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    user_q = st.chat_input("Frage stellen (z. B. „liste klassen“, „liste IfcPump“, „psets id …“)")
    if user_q:
        st.session_state.chat.append({"role": "user", "content": user_q})
        resp = answer(user_q, ifc, gindex)
        st.session_state.chat.append({"role": "assistant", "content": resp})
        st.rerun()
