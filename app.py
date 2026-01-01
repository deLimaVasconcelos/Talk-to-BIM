# app.py
# -*- coding: utf-8 -*-

import os
import re
import tempfile
from collections import defaultdict
from typing import Dict, Any, List, Optional

import streamlit as st

try:
    import ifcopenshell  # type: ignore
except Exception:
    ifcopenshell = None


# ============================================================
# Konfiguration: GA-relevante IFC-Klassen (nur Klassenlogik)
# ============================================================
GA_CLASSES: Dict[str, set] = {
    "lüftung": {"IfcDuctSegment", "IfcDuctFitting", "IfcFan", "IfcAirTerminal", "IfcDamper"},
    "heizen": {"IfcRadiator", "IfcBoiler", "IfcPipeSegment", "IfcHeatExchanger", "IfcBuildingElementProxy"},
    "kühlen": {"IfcChiller", "IfcCoolingTower", "IfcCoil", "IfcCovering"},
    "licht": {"IfcLightFixture", "IfcLamp"},
    "steuerung": {"IfcSensor", "IfcActuator", "IfcController", "IfcAlarm", "IfcUnitaryControlElement"},
}


# ============================================================
# Hilfsfunktionen
# ============================================================
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
      - IfcRelDefinesByProperties -> IfcPropertySet -> IfcPropertySingleValue
    Bei komplexeren Datentypen kann später erweitert werden.
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


def classify_ga(element) -> Optional[str]:
    cls = element.is_a()
    for cat, clsset in GA_CLASSES.items():
        if cls in clsset:
            return cat
    return None


def build_room_index(ifc) -> Dict[str, Any]:
    """
    Indexstruktur:
      rooms[rid] = { room_id, room_name, room_longname, ga_items[] }
      global_lookup[gid] = minimal meta
    """
    rooms = ifc.by_type("IfcSpace") or []
    room_by_gid = {r.GlobalId: r for r in rooms if getattr(r, "GlobalId", None)}

    # Containment: Raum -> Elemente
    contained: Dict[str, List[Any]] = defaultdict(list)
    for rel in ifc.by_type("IfcRelContainedInSpatialStructure") or []:
        try:
            container = rel.RelatingStructure
            if container and container.is_a("IfcSpace"):
                rid = safe_str(getattr(container, "GlobalId", ""))
                for e in (rel.RelatedElements or []):
                    contained[rid].append(e)
        except Exception:
            continue

    index: Dict[str, Any] = {"rooms": {}, "global_lookup": {}}

    for rid, room in room_by_gid.items():
        rname = safe_str(getattr(room, "Name", "")) or f"Space_{rid}"
        rlong = safe_str(getattr(room, "LongName", ""))

        ga_items: List[Dict[str, Any]] = []

        for e in contained.get(rid, []):
            try:
                gid = safe_str(getattr(e, "GlobalId", ""))
                cls = e.is_a()
                cat = classify_ga(e)

                meta = {
                    "global_id": gid,
                    "ifc_class": cls,
                    "name": safe_str(getattr(e, "Name", "")),
                    "object_type": safe_str(getattr(e, "ObjectType", "")),
                    "predefined_type": safe_str(getattr(e, "PredefinedType", "")),
                }
                if gid:
                    index["global_lookup"][gid] = meta

                if cat:
                    meta_ga = dict(meta)
                    meta_ga["category"] = cat
                    meta_ga["psets"] = get_psets(e)
                    ga_items.append(meta_ga)

            except Exception:
                continue

        index["rooms"][rid] = {
            "room_id": rid,
            "room_name": rname,
            "room_longname": rlong,
            "ga_items": ga_items,
        }

    return index


def best_room_match(question: str, index: Dict[str, Any]) -> Optional[str]:
    """
    Raum-Matching über enthält-Logik in Name/LongName.
    Für Workshops ausreichend; später ggf. Dropdown-Kontext.
    """
    q = normalize(question)
    best_rid = None
    best_len = 0

    for rid, r in index.get("rooms", {}).items():
        rn = normalize(r.get("room_name", ""))
        rl = normalize(r.get("room_longname", ""))

        for cand in (rn, rl):
            if cand and cand in q and len(cand) > best_len:
                best_rid = rid
                best_len = len(cand)

    return best_rid


def format_ga_items(items: List[Dict[str, Any]], show_psets: bool = False) -> str:
    if not items:
        return "Keine GA-relevanten Objekte erkannt."
    lines = []
    for it in items:
        lines.append(
            f"- [{it['category']}] {it['ifc_class']} | {it.get('name','')} | {it.get('global_id','')}"
        )
        if show_psets:
            psets = it.get("psets", {}) or {}
            for psn, props in psets.items():
                if not props:
                    continue
                sample = list(props.items())[:8]
                lines.append(f"  - {psn}: " + ", ".join([f"{k}={v}" for k, v in sample]))
    return "\n".join(lines)


# ============================================================
# Query Engine (deterministisch, leicht erweiterbar)
# ============================================================
def answer(question: str, index: Dict[str, Any]) -> str:
    q = normalize(question)

    if q in {"hilfe", "help", "?"}:
        return (
            "**Mögliche Abfragen:**\n"
            "- `liste räume`\n"
            "- `ga übersicht` / `ga in jedem raum`\n"
            "- `ga in raum <name>`\n"
            "- `lüftung|heizen|kühlen|licht|steuerung in raum <name>`\n"
            "- `suche \"text\"` (durchsucht Name/ObjectType/PredefinedType/Klasse)\n"
            "- `id <GlobalId>`\n"
            "- `details id <GlobalId>`\n"
            "- `psets id <GlobalId>`\n"
        )

    if "liste räume" in q or "räume auflisten" in q:
        rooms = list(index.get("rooms", {}).values())
        if not rooms:
            return "Es wurden keine IfcSpace-Objekte (Räume) erkannt."
        lines = ["**Erkannte Räume:**"]
        for r in sorted(rooms, key=lambda x: normalize(x.get("room_name", ""))):
            lines.append(f"- {r['room_name']} ({r['room_id']})")
        return "\n".join(lines)

    if "ga übersicht" in q or "ga in jedem raum" in q or "in jedem raum" in q:
        rooms = index.get("rooms", {})
        if not rooms:
            return "Es wurden keine Räume erkannt."
        lines = ["**GA-Übersicht je Raum (Anzahl pro Kategorie):**"]
        for rid, r in sorted(rooms.items(), key=lambda kv: normalize(kv[1].get("room_name", ""))):
            counts = defaultdict(int)
            for it in r.get("ga_items", []):
                counts[it["category"]] += 1
            if counts:
                parts = ", ".join([f"{k}={counts[k]}" for k in sorted(counts.keys())])
            else:
                parts = "keine GA-Objekte erkannt"
            lines.append(f"- {r.get('room_name','')} : {parts}")
        return "\n".join(lines)

    # Minimalinfo über GlobalId
    m = re.search(r"\b(id|globalid)\s+([0-9a-zA-Z_$]{6,})\b", q)
    if m and "details" not in q and "psets" not in q:
        gid = m.group(2)
        hit = index.get("global_lookup", {}).get(gid)
        if not hit:
            return f"Kein Element mit GlobalId `{gid}` gefunden."
        return (
            "**Element gefunden:**\n"
            f"- Klasse: {hit.get('ifc_class','')}\n"
            f"- Name: {hit.get('name','')}\n"
            f"- ObjectType: {hit.get('object_type','')}\n"
            f"- PredefinedType: {hit.get('predefined_type','')}\n"
            f"- GlobalId: {gid}\n"
            "Für Details: `details id <GlobalId>` oder `psets id <GlobalId>`."
        )

    # Details zu GA-Objekt (Raum + Kategorie)
    m2 = re.search(r"\bdetails\s+id\s+([0-9a-zA-Z_$]{6,})\b", q)
    if m2:
        gid = m2.group(1)
        for r in index.get("rooms", {}).values():
            for it in r.get("ga_items", []):
                if it.get("global_id") == gid:
                    return (
                        f"**Details (GA) – {gid}:**\n"
                        f"- Raum: {r.get('room_name','')}\n"
                        f"- Kategorie: {it.get('category','')}\n"
                        f"- IFC-Klasse: {it.get('ifc_class','')}\n"
                        f"- Name: {it.get('name','')}\n"
                        f"- ObjectType: {it.get('object_type','')}\n"
                        f"- PredefinedType: {it.get('predefined_type','')}\n"
                    )
        return f"Keine GA-Details zu `{gid}` gefunden (ggf. ist es kein GA-klassifiziertes Objekt)."

    # Psets zu GA-Objekt
    m3 = re.search(r"\bpsets\s+id\s+([0-9a-zA-Z_$]{6,})\b", q)
    if m3:
        gid = m3.group(1)
        for r in index.get("rooms", {}).values():
            for it in r.get("ga_items", []):
                if it.get("global_id") == gid:
                    psets = it.get("psets", {}) or {}
                    if not psets:
                        return f"Für `{gid}` wurden keine PropertySets gefunden."
                    lines = [f"**PropertySets – {gid}:**"]
                    for psn, props in psets.items():
                        lines.append(f"- {psn}")
                        for k, v in list(props.items())[:30]:
                            lines.append(f"  - {k}: {v}")
                    return "\n".join(lines)
        return f"Keine PropertySets zu `{gid}` gefunden (ggf. ist es kein GA-klassifiziertes Objekt)."

    # Textsuche
    m4 = re.search(r'suche\s+"([^"]+)"', question, flags=re.IGNORECASE)
    if m4:
        needle_raw = m4.group(1)
        needle = normalize(needle_raw)
        if not needle:
            return "Bitte einen Suchbegriff angeben, z. B. `suche \"VAV\"`."
        hits = []
        for r in index.get("rooms", {}).values():
            for it in r.get("ga_items", []):
                hay = " ".join([
                    normalize(it.get("name", "")),
                    normalize(it.get("object_type", "")),
                    normalize(it.get("predefined_type", "")),
                    normalize(it.get("ifc_class", "")),
                ])
                if needle in hay:
                    hits.append((r.get("room_name", ""), it))

        if not hits:
            return f"Keine Treffer für „{needle_raw}“ in GA-Objekten gefunden."

        lines = [f"**Treffer für „{needle_raw}“:**"]
        for rn, it in hits[:60]:
            lines.append(f"- Raum: {rn} | [{it['category']}] {it['ifc_class']} | {it.get('name','')} | {it.get('global_id','')}")
        return "\n".join(lines)

    # Kategorie in Raum
    for cat in ["lüftung", "heizen", "kühlen", "licht", "steuerung"]:
        if cat in q and "raum" in q:
            rid = best_room_match(q, index)
            if not rid:
                return "Der Raum konnte nicht eindeutig erkannt werden. Bitte den exakten Raumnamen verwenden oder zuvor `liste räume` ausführen."
            r = index["rooms"][rid]
            items = [it for it in r.get("ga_items", []) if it.get("category") == cat]
            return f"**{cat.capitalize()} – Raum „{r.get('room_name','')}“:**\n" + format_ga_items(items)

    # GA in Raum (ohne Kategorie)
    if "ga" in q and "raum" in q:
        rid = best_room_match(q, index)
        if not rid:
            return "Der Raum konnte nicht eindeutig erkannt werden. Bitte den exakten Raumnamen verwenden oder zuvor `liste räume` ausführen."
        r = index["rooms"][rid]
        return f"**GA-relevante Objekte – Raum „{r.get('room_name','')}“:**\n" + format_ga_items(r.get("ga_items", []))

    return (
        "Ich kann die Frage noch nicht eindeutig zuordnen.\n"
        "Nutzen Sie `hilfe` für Beispiele oder formulieren Sie z. B.:\n"
        "- `ga in jedem raum`\n"
        "- `ga in raum <Raumname>`\n"
        "- `lüftung in raum <Raumname>`\n"
        "- `suche \"<Text>\"`"
    )


# ============================================================
# Streamlit UI
# ============================================================
st.set_page_config(page_title="IFC GA Chat", page_icon="💬", layout="wide")
st.title("IFC GA Chat")

if ifcopenshell is None:
    st.error("IfcOpenShell ist nicht verfügbar. Bitte `ifcopenshell` in `requirements.txt` aufnehmen.")
    st.stop()

with st.sidebar:
    st.header("IFC laden")

    use_default = st.toggle("Repo-Datei verwenden (default.ifc)", value=True)
    default_path = "default.ifc"

    uploaded = None
    if not use_default:
        uploaded = st.file_uploader("IFC hochladen", type=["ifc", "IFC"])

    if use_default:
        if not os.path.exists(default_path):
            st.warning("default.ifc wurde im Repo nicht gefunden. Datei hinzufügen oder Toggle deaktivieren.")
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

    with st.spinner("Index wird aufgebaut …"):
        index = build_room_index(ifc)

    st.success(f"Geladen. Räume erkannt: {len(index.get('rooms', {}))}")

    st.write("---")
    st.subheader("GA-Kategorien (Gesamt)")
    counts_all = defaultdict(int)
    for r in index.get("rooms", {}).values():
        for it in r.get("ga_items", []):
            counts_all[it["category"]] += 1
    if counts_all:
        for k in ["lüftung", "kühlen", "heizen", "licht", "steuerung"]:
            st.write(f"- {k}: {counts_all.get(k, 0)}")
    else:
        st.write("Keine GA-Objekte erkannt (nach aktueller Klassenliste).")

    st.write("---")
    st.caption("Chat-Beispiele: `hilfe`, `liste räume`, `ga in jedem raum`.")


st.subheader("Chat")

if "chat" not in st.session_state:
    st.session_state.chat = [
        {"role": "assistant", "content": "Geben Sie `hilfe` ein, um mögliche Abfragen zu sehen."}
    ]

for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

user_q = st.chat_input("Frage stellen (z. B. „ga in jedem raum“ oder „lüftung in raum Büro 1.01“)")

if user_q:
    st.session_state.chat.append({"role": "user", "content": user_q})
    response = answer(user_q, index)
    st.session_state.chat.append({"role": "assistant", "content": response})
    st.rerun()
