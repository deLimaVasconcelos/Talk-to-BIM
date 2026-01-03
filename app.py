# app.py
# -*- coding: utf-8 -*-

import base64
import streamlit as st

st.set_page_config(page_title="Talk2BIM – IFC Viewer", page_icon="🧩", layout="wide")
st.title("Talk2BIM – IFC Viewer")

st.caption(
    "Dieser Viewer rendert clientseitig (im Browser). Dadurch bleibt die Streamlit-Cloud "
    "speicherschonend und große IFCs sind besser handhabbar."
)

uploaded = st.file_uploader("IFC-Datei hochladen", type=["ifc", "IFC"])

if uploaded is None:
    st.info("Bitte eine IFC-Datei auswählen. Danach wird das Modell direkt im Viewer geladen.")
    st.stop()

ifc_bytes = uploaded.getvalue()
if not ifc_bytes:
    st.error("Die hochgeladene Datei ist leer.")
    st.stop()

# Base64 als Transport in die HTML-Komponente
ifc_b64 = base64.b64encode(ifc_bytes).decode("utf-8")

# KEIN f-string! Wir ersetzen nur einen Marker.
html_template = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    html, body {
      margin: 0;
      padding: 0;
      height: 100%;
      background: #ffffff;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    }
    #wrap {
      height: 78vh;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      overflow: hidden;
      position: relative;
      background: #fff;
    }
    #c {
      width: 100%;
      height: 100%;
      display: block;
    }
    #hint {
      position:absolute;
      left:12px;
      top:12px;
      background: rgba(255,255,255,0.92);
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 8px 10px;
      font-size: 13px;
      color: #111827;
      z-index: 5;
    }
    #status {
      position:absolute;
      right:12px;
      top:12px;
      background: rgba(255,255,255,0.92);
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 8px 10px;
      font-size: 13px;
      color: #111827;
      z-index: 5;
      max-width: 45%;
      text-align: left;
      white-space: pre-wrap;
    }
  </style>
</head>
<body>
  <div id="wrap">
    <div id="hint">Maus: Drehen (links) · Schwenken (rechts) · Zoom (Rad)</div>
    <div id="status">Lade IFC …</div>
    <canvas id="c"></canvas>
  </div>

  <script type="module">
    import * as THREE from "https://unpkg.com/three@0.160.0/build/three.module.js";
    import { OrbitControls } from "https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js";
    import { IFCLoader } from "https://unpkg.com/web-ifc-three@0.0.152/IFCLoader.js";

    const statusEl = document.getElementById("status");
    const canvas = document.getElementById("c");

    // --- THREE Setup
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0xffffff, 1);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xffffff);

    const camera = new THREE.PerspectiveCamera(60, 2, 0.1, 5000);
    camera.position.set(12, 10, 12);

    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    const hemi = new THREE.HemisphereLight(0xffffff, 0x444444, 1.0);
    hemi.position.set(0, 50, 0);
    scene.add(hemi);

    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(10, 20, 10);
    scene.add(dir);

    const grid = new THREE.GridHelper(50, 50, 0xdddddd, 0xeeeeee);
    scene.add(grid);

    function resize() {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== w || canvas.height !== h) {
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
    }

    // --- Base64 -> Blob URL
    function base64ToUint8Array(base64) {
      const raw = atob(base64);
      const arr = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
      return arr;
    }

    const IFC_B64 = "__IFC_B64__";
    const ifcBytes = base64ToUint8Array(IFC_B64);
    const blob = new Blob([ifcBytes], { type: "application/octet-stream" });
    const blobUrl = URL.createObjectURL(blob);

    // --- IFC Loader
    const ifcLoader = new IFCLoader();
    ifcLoader.ifcManager.setWasmPath("https://unpkg.com/web-ifc@0.0.57/");

    statusEl.textContent = "Lade IFC … (Parsing im Browser)";

    ifcLoader.load(
      blobUrl,
      (model) => {
        scene.add(model);

        const box = new THREE.Box3().setFromObject(model);
        const size = box.getSize(new THREE.Vector3());
        const center = box.getCenter(new THREE.Vector3());

        const maxDim = Math.max(size.x, size.y, size.z);
        const fov = camera.fov * (Math.PI / 180);
        let camDist = Math.abs(maxDim / 2 / Math.tan(fov / 2));
        camDist *= 1.4;

        camera.near = Math.max(0.01, maxDim / 1000);
        camera.far  = Math.max(5000, maxDim * 10);
        camera.updateProjectionMatrix();

        camera.position.set(center.x + camDist, center.y + camDist * 0.6, center.z + camDist);
        controls.target.set(center.x, center.y, center.z);
        controls.update();

        statusEl.textContent = "IFC geladen.";
        URL.revokeObjectURL(blobUrl);
      },
      (xhr) => {
        if (xhr && xhr.total) {
          const p = Math.round((xhr.loaded / xhr.total) * 100);
          statusEl.textContent = "Lade IFC … " + p + "%";
        }
      },
      (err) => {
        console.error(err);
        statusEl.textContent =
          "Fehler beim Laden der IFC.\\n" +
          "Bitte Browser-Konsole prüfen (F12 → Console).\\n" +
          "Mögliche Ursachen: IFC beschädigt oder Web-IFC/WASM nicht erreichbar.";
      }
    );

    function animate() {
      resize();
      controls.update();
      renderer.render(scene, camera);
      requestAnimationFrame(animate);
    }
    requestAnimationFrame(animate);

    window.addEventListener("resize", resize);
  </script>
</body>
</html>
"""

html = html_template.replace("__IFC_B64__", ifc_b64)

st.components.v1.html(html, height=850, scrolling=False)
