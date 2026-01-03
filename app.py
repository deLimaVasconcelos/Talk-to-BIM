# app.py
# -*- coding: utf-8 -*-

import base64
import streamlit as st

st.set_page_config(page_title="Talk2BIM – IFC Viewer", page_icon="🧩", layout="wide")
st.title("Talk2BIM – IFC Viewer")

st.caption(
    "Hinweis: Dieser Viewer rendert clientseitig (im Browser). "
    "Dadurch bleibt die Streamlit-Cloud speicherschonend und große IFCs sind besser handhabbar."
)

uploaded = st.file_uploader("IFC-Datei hochladen", type=["ifc", "IFC"])

if uploaded is None:
    st.info("Bitte eine IFC-Datei auswählen. Danach erscheint das Modell direkt im Viewer.")
    st.stop()

# IFC Bytes -> Base64 (für Übergabe an Browser/JS)
ifc_bytes = uploaded.getvalue()
if not ifc_bytes:
    st.error("Die hochgeladene Datei ist leer.")
    st.stop()

ifc_b64 = base64.b64encode(ifc_bytes).decode("utf-8")

# Viewer HTML (IFC.js via CDN). Rendering passiert im Browser.
# Steuerung: OrbitControls, einfache Beleuchtung, auto-fit.
html = f"""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Talk2BIM Viewer</title>
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      height: 100%;
      background: #ffffff;
      font-family: sans-serif;
    }}
    #wrap {{
      height: 78vh;
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      overflow: hidden;
      position: relative;
    }}
    #c {{
      width: 100%;
      height: 100%;
      display: block;
    }}
    #hint {{
      position:absolute;
      left:12px;
      top:12px;
      background: rgba(255,255,255,0.85);
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 8px 10px;
      font-size: 13px;
      color: #111827;
    }}
  </style>
</head>
<body>
  <div id="wrap">
    <div id="hint">Maus: Drehen (links) · Schwenken (rechts) · Zoom (Rad)</div>
    <canvas id="c"></canvas>
  </div>

  <script type="module">
    import * as THREE from "https://unpkg.com/three@0.160.0/build/three.module.js";
    import {{ OrbitControls }} from "https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js";

    // IFC.js (web-ifc + loader)
    import {{ IFCLoader }} from "https://unpkg.com/web-ifc-three@0.0.152/IFCLoader.js";

    // Canvas / Renderer
    const canvas = document.getElementById("c");
    const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true, alpha: true }});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xffffff);

    // Camera
    const camera = new THREE.PerspectiveCamera(60, 2, 0.1, 5000);
    camera.position.set(12, 10, 12);

    // Controls
    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    // Lights
    const hemi = new THREE.HemisphereLight(0xffffff, 0x444444, 1.0);
    hemi.position.set(0, 50, 0);
    scene.add(hemi);

    const dir = new THREE.DirectionalLight(0xffffff, 0.8);
    dir.position.set(10, 20, 10);
    scene.add(dir);

    // Simple grid (optional)
    const grid = new THREE.GridHelper(50, 50, 0xdddddd, 0xeeeeee);
    grid.position.y = 0;
    scene.add(grid);

    // Resize
    function resizeRendererToDisplaySize() {{
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      const needResize = canvas.width !== width || canvas.height !== height;
      if (needResize) {{
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
      }}
      return needResize;
    }}

    // IFC Loader
    const ifcLoader = new IFCLoader();
    // Web-IFC WASM Pfad (CDN)
    ifcLoader.ifcManager.setWasmPath("https://unpkg.com/web-ifc@0.0.57/");

    // Base64 -> ArrayBuffer
    function base64ToArrayBuffer(base64) {{
      const binary_string = atob(base64);
      const len = binary_string.length;
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) {{
        bytes[i] = binary_string.charCodeAt(i);
      }}
      return bytes.buffer;
    }}

    const ifcData = base64ToArrayBuffer("{ifc_b64}");

    // Load IFC from buffer
    // web-ifc-three unterstützt parse von buffer via ifcManager.parse
    // Wir erzeugen daraus ein Three.js Mesh.
    const modelID = await ifcLoader.ifcManager.parse(ifcData);
    const model = await ifcLoader.ifcManager.getModel(modelID);
    scene.add(model);

    // Fit to model
    const box = new THREE.Box3().setFromObject(model);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());

    const maxDim = Math.max(size.x, size.y, size.z);
    const fov = camera.fov * (Math.PI / 180);
    let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));
    cameraZ *= 1.4;

    camera.position.set(center.x + cameraZ, center.y + cameraZ * 0.6, center.z + cameraZ);
    camera.near = Math.max(0.1, maxDim / 1000);
    camera.far = Math.max(5000, maxDim * 10);
    camera.updateProjectionMatrix();

    controls.target.set(center.x, center.y, center.z);
    controls.update();

    // Render loop
    function render() {{
      resizeRendererToDisplaySize();
      controls.update();
      renderer.render(scene, camera);
      requestAnimationFrame(render);
    }}
    requestAnimationFrame(render);

    // Handle resize
    window.addEventListener("resize", () => resizeRendererToDisplaySize());
  </script>
</body>
</html>
"""

# Streamlit: HTML einbetten
st.components.v1.html(html, height=800, scrolling=False)
