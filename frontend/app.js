const sourceLabels = {
  point: "点源",
  line: "线源",
  surface: "面源",
  volume: "体源",
  all: "全部四类",
};

const moduleLabels = {
  target: "UUV辐射噪声",
  environment: "海洋背景噪声",
};

const state = {
  selectedModule: "target",
  selectedSource: "point",
  selectedResultTab: "overview",
  currentJob: null,
  currentCase: null,
  pollTimer: null,
};

let geometry3D = null;

const $ = (id) => document.getElementById(id);

function num(id) {
  const v = Number($(id).value);
  return Number.isFinite(v) ? v : undefined;
}

function fmt(n, d = 2) {
  if (typeof n !== "number" || !Number.isFinite(n)) return "-";
  return n.toFixed(d);
}

function statusLabel(s) {
  return { queued: "等待中", running: "运行中", succeeded: "成功", failed: "失败" }[s] || s || "-";
}

/* ===== Source Selection ===== */
function setModule(moduleName) {
  state.selectedModule = moduleName;
  document.querySelectorAll(".module-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.module === moduleName);
  });
  document.querySelectorAll(".target-config").forEach((el) => {
    el.classList.toggle("hidden", moduleName !== "target");
  });
  document.querySelectorAll(".environment-config").forEach((el) => {
    el.classList.toggle("hidden", moduleName !== "environment");
  });
  $("configTitle").textContent = moduleName === "environment" ? "海洋背景噪声配置" : "UUV 参数配置";
}

function bindModuleButtons() {
  document.querySelectorAll(".module-btn").forEach((btn) => {
    btn.addEventListener("click", () => setModule(btn.dataset.module));
  });
}

function setSource(src) {
  state.selectedSource = src;
  document.querySelectorAll(".source-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.source === src);
  });
}

function bindSourceButtons() {
  document.querySelectorAll(".source-btn").forEach((btn) => {
    btn.addEventListener("click", () => setSource(btn.dataset.source));
  });
}

/* ===== Result Tabs ===== */
function setResultTab(tab) {
  state.selectedResultTab = tab;
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll(".result-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.dataset.pane === tab);
  });
  if (tab === "geometry" && state.currentCase) {
    requestAnimationFrame(() => renderGeometry3D(state.currentCase));
  }
}

function bindResultTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => setResultTab(btn.dataset.tab));
  });
}

/* ===== Health Check ===== */
async function checkHealth() {
  const chip = $("healthChip");
  const textEl = chip.querySelector(".status-text");
  try {
    const res = await fetch("/api/health");
    const h = await res.json();
    const ok = h.engine === "python";
    textEl.textContent = ok ? "Python 可用" : "后端异常";
    chip.classList.toggle("bad", !ok);
  } catch {
    textEl.textContent = "后端未连接";
    chip.classList.add("bad");
  }
}

/* ===== Collect Config ===== */
function collectConfig() {
  if (state.selectedModule === "environment") {
    return {
      module: "environment",
      fs: num("env_fs"),
      duration_s: num("env_duration_s"),
      random_seed: num("env_random_seed"),
      environment: {
        components: {
          wind: $("env_wind_enabled").checked,
          shipping: $("env_shipping_enabled").checked,
          rain: $("env_rain_enabled").checked,
          thermal: $("env_thermal_enabled").checked,
        },
        wind_speed_mps: num("env_wind_speed_mps"),
        shipping_activity: num("env_shipping_activity"),
        rain_rate_mm_h: num("env_rain_rate_mm_h"),
        gain_db: num("env_gain_db"),
      },
    };
  }

  return {
    module: "target",
    source_type: state.selectedSource,
    fs: num("fs"),
    duration_s: num("duration_s"),
    random_seed: num("random_seed"),
    uuv: {
      length_m: num("length_m"),
      diameter_m: num("diameter_m"),
      depth_m: num("depth_m"),
      speed_mps: num("speed_mps"),
      rpm: num("rpm"),
      blade_count: num("blade_count"),
      propeller_diameter_m: num("propeller_diameter_m"),
      propeller_pitch_m: num("propeller_pitch_m"),
      displacement_t: num("displacement_t"),
    },
    source: {
      line_elements: num("line_elements"),
      surface_axial_elements: num("surface_axial_elements"),
      surface_circum_elements: num("surface_circum_elements"),
      volume_axial_elements: num("volume_axial_elements"),
      volume_radial_elements: num("volume_radial_elements"),
      volume_circum_elements: num("volume_circum_elements"),
    },
    receiver: {
      x_m: num("receiver_x_m"),
      y_m: num("receiver_y_m"),
      z_m: num("receiver_z_m"),
    },
    ambient: {
      enabled: $("ambient_enabled").checked,
      rms_uPa: num("ambient_rms_uPa"),
      slope_db_decade: num("ambient_slope_db_decade"),
    },
  };
}

/* ===== Render Results ===== */
function fileUrl(c, k) {
  return c.file_urls && c.file_urls[k] ? c.file_urls[k] : "";
}

function renderOverview(caseData) {
  if (caseData.module === "environment") return renderEnvironmentOverview(caseData);
  const f = caseData.features || {};
  const m = caseData.metrics || {};
  const g = caseData.geometry || {};
  const components = Array.isArray(caseData.noise_components)
    ? caseData.noise_components.join("、")
    : "机械宽带 / 线谱 / 空化连续谱 / 流噪声";

  return `
    <div class="metric-row">
      <div class="metric-box"><span>轴频</span><strong>${fmt(f.shaft_hz)} Hz</strong></div>
      <div class="metric-box"><span>叶频 BPF</span><strong>${fmt(f.bpf_hz)} Hz</strong></div>
      <div class="metric-box"><span>空化指数</span><strong>${fmt(f.cavitation_activity)}</strong></div>
      <div class="metric-box"><span>预览 SNR</span><strong>${fmt(m.preview_snr_db)} dB</strong></div>
    </div>
    ${fileUrl(caseData, "summary_png") ? `<div class="result-image"><img src="${fileUrl(caseData, "summary_png")}" alt="总览" /></div>` : ""}
    <div class="overview-note">
      输出的是 UUV 目标辐射噪声半经验信号，包含 ${components}。<br/>
      received_mix 额外叠加背景噪声，仅用于接收端混合预览。
      <br/><br/>
      <strong>${g.num_elements || "-"}</strong> 个离散单元 · 耗时 <strong>${fmt(caseData.elapsed_s, 1)} s</strong>
    </div>
  `;
}

function renderEnvironmentOverview(caseData) {
  const f = caseData.features || {};
  const m = caseData.metrics || {};
  const components = Array.isArray(caseData.noise_components) && caseData.noise_components.length
    ? caseData.noise_components.join("、")
    : "未选择";
  return `
    <div class="metric-row">
      <div class="metric-box"><span>风速</span><strong>${fmt(f.wind_speed_mps)} m/s</strong></div>
      <div class="metric-box"><span>航运强度</span><strong>${fmt(f.shipping_activity)}</strong></div>
      <div class="metric-box"><span>雨强</span><strong>${fmt(f.rain_rate_mm_h)} mm/h</strong></div>
      <div class="metric-box"><span>混合 RMS</span><strong>${fmt(m.mix_rms_uPa)} μPa</strong></div>
    </div>
    ${fileUrl(caseData, "summary_png") ? `<div class="result-image"><img src="${fileUrl(caseData, "summary_png")}" alt="海洋背景噪声总览" /></div>` : ""}
    <div class="overview-note">
      当前输出为海洋环境背景噪声，已选择：${components}。<br/>
      每类噪声独立生成 WAV，另输出总混合 WAV、频谱 CSV、时域 CSV 与 NPZ 结果。
      <br/><br/>
      主导分量：<strong>${m.peak_component || "-"}</strong> · 耗时 <strong>${fmt(caseData.elapsed_s, 1)} s</strong>
    </div>
  `;
}

function renderImagePane(caseData, key) {
  const url = fileUrl(caseData, key);
  if (!url) return `<div class="empty-state simple"><h4>暂无图片</h4><p>该结果暂不可用</p></div>`;
  return `<div class="result-image"><img src="${url}" alt="" /></div>`;
}

function renderAudioPane(caseData) {
  if (caseData.module === "environment") {
    const items = [
      ["环境噪声混合", fileUrl(caseData, "mix_wav")],
      ["风浪噪声", fileUrl(caseData, "wind_wav")],
      ["航运噪声", fileUrl(caseData, "shipping_wav")],
      ["雨噪声", fileUrl(caseData, "rain_wav")],
      ["热噪声", fileUrl(caseData, "thermal_wav")],
    ].filter(([, u]) => u);
    return items.map(([title, url]) => `
      <div class="audio-item">
        <span>${title}</span>
        <audio controls src="${url}"></audio>
      </div>
    `).join("");
  }

  const items = [
    ["1 m 等效源信号", fileUrl(caseData, "source_wav")],
    ["传播后目标预览", fileUrl(caseData, "received_target_wav")],
    ["目标 + 背景混合预览", fileUrl(caseData, "received_mix_wav")],
  ].filter(([, u]) => u);

  if (!items.length) return `<div class="empty-state simple"><h4>暂无音频</h4><p>运行仿真后可试听</p></div>`;

  return items.map(([title, url]) => `
    <div class="audio-item">
      <span>${title}</span>
      <audio controls src="${url}"></audio>
    </div>
  `).join("");
}

function renderFilesPane(caseData) {
  if (caseData.module === "environment") {
    const items = [
      ["频谱 CSV", fileUrl(caseData, "spectrum_csv")],
      ["时域 CSV", fileUrl(caseData, "timeseries_csv")],
      ["NPZ 结果", fileUrl(caseData, "npz_result")],
      ["说明 TXT", fileUrl(caseData, "description_txt")],
      ["运行日志", fileUrl(caseData, "log_file")],
    ].filter(([, u]) => u);
    if (!items.length) return `<div class="empty-state simple"><h4>暂无文件</h4><p>运行仿真后可下载</p></div>`;
    return `<div class="file-grid">${items.map(([t, u]) => `
      <a class="file-item" href="${u}" target="_blank" rel="noreferrer">${t}</a>
    `).join("")}</div>`;
  }

  const items = [
    ["源级谱 CSV", fileUrl(caseData, "spectrum_csv")],
    ["线谱表 CSV", fileUrl(caseData, "tones_csv")],
    ["几何 CSV", fileUrl(caseData, "geometry_csv")],
    ["NPZ 结果", fileUrl(caseData, "npz_result")],
    ["说明 TXT", fileUrl(caseData, "description_txt")],
    ["运行日志", fileUrl(caseData, "log_file")],
  ].filter(([, u]) => u);

  if (!items.length) return `<div class="empty-state simple"><h4>暂无文件</h4><p>运行仿真后可下载</p></div>`;

  return `<div class="file-grid">${items.map(([t, u]) => `
    <a class="file-item" href="${u}" target="_blank" rel="noreferrer">${t}</a>
  `).join("")}</div>`;
}

function emptyHtml(title, desc) {
  return `
    <div class="empty-state">
      <div class="empty-icon">
        <svg viewBox="0 0 120 120" fill="none">
          <circle cx="60" cy="60" r="50" stroke="currentColor" stroke-width="1.5" stroke-dasharray="6 4" opacity="0.2"/>
          <circle cx="60" cy="60" r="36" stroke="currentColor" stroke-width="1.5" opacity="0.15"/>
          <circle cx="60" cy="60" r="22" fill="currentColor" opacity="0.1"/>
          <path d="M52 55l6 8 12-14" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.4"/>
        </svg>
      </div>
      <h3>${title}</h3>
      <p>${desc}</p>
    </div>
  `;
}

/* ===== 3D Geometry Rendering (Three.js) ===== */
function computeBounds(points) {
  let xmin = Infinity, ymin = Infinity, zmin = Infinity;
  let xmax = -Infinity, ymax = -Infinity, zmax = -Infinity;
  for (const p of points) {
    if (p[0] < xmin) xmin = p[0]; if (p[0] > xmax) xmax = p[0];
    if (p[1] < ymin) ymin = p[1]; if (p[1] > ymax) ymax = p[1];
    if (p[2] < zmin) zmin = p[2]; if (p[2] > zmax) zmax = p[2];
  }
  return {
    center: [(xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2],
    size: Math.max(xmax - xmin, ymax - ymin, zmax - zmin, 10),
  };
}

function cleanupGeometry3D() {
  if (!geometry3D) return;
  const { renderer, controls, onResize } = geometry3D;
  window.removeEventListener("resize", onResize);
  controls.dispose();
  renderer.dispose();
  const container = $("geometry3dContainer");
  if (container) container.innerHTML = "";
  geometry3D = null;
}

function renderGeometry3D(caseData) {
  if (!window.THREE) {
    console.warn("Three.js not loaded");
    return;
  }

  const container = $("geometry3dContainer");
  if (!container) return;

  cleanupGeometry3D();

  const geom = caseData.geometry;
  if (!geom || !geom.element_xyz_m || !geom.element_xyz_m.length) {
    container.innerHTML = "";
    const info = $("geometry3dInfo");
    if (info) info.innerHTML = "<span>无几何数据</span>";
    return;
  }

  const elements = geom.element_xyz_m;
  const weights = geom.weight || elements.map(() => 1);
  const receiver = geom.receiver_xyz_m || [600, 160, -45];
  const length = geom.uuv_length_m || 3.2;
  const diameter = geom.uuv_diameter_m || 0.45;
  const center = geom.source_center_xyz_m || [0, 0, -50];
  const range = geom.range_m || 0;
  const sourceType = geom.type || "point";

  const allPoints = [...elements, receiver, center];
  const bounds = computeBounds(allPoints);
  const viewCenter = bounds.center;
  const viewSize = Math.max(bounds.size, 20);

  const rect = container.getBoundingClientRect();
  const w = rect.width > 0 ? rect.width : 600;
  const h = rect.height > 0 ? rect.height : 400;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0f172a);
  scene.fog = new THREE.Fog(0x0f172a, viewSize * 3, viewSize * 10);

  const camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 5000);
  camera.position.set(
    viewCenter[0] + viewSize * 0.6,
    viewCenter[1] + viewSize * 0.4,
    viewCenter[2] + viewSize * 0.6
  );

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(w, h);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  container.appendChild(renderer.domElement);

  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.target.set(viewCenter[0], viewCenter[1], viewCenter[2]);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.update();

  scene.add(new THREE.AmbientLight(0x64748b, 0.7));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
  dirLight.position.set(1, 2, 1);
  scene.add(dirLight);
  const dirLight2 = new THREE.DirectionalLight(0x60a5fa, 0.3);
  dirLight2.position.set(-1, -1, -1);
  scene.add(dirLight2);

  const gridHelper = new THREE.GridHelper(viewSize * 2, 20, 0x334155, 0x1e293b);
  gridHelper.position.set(viewCenter[0], viewCenter[1], viewCenter[2]);
  scene.add(gridHelper);

  const axesHelper = new THREE.AxesHelper(Math.max(viewSize * 0.15, 5));
  axesHelper.position.set(viewCenter[0], viewCenter[1], viewCenter[2]);
  scene.add(axesHelper);

  const hullRadius = Math.max(diameter / 2, 0.1);
  const hullLength = Math.max(length, 0.5);
  const hullGeom = new THREE.CylinderGeometry(hullRadius, hullRadius * 0.7, hullLength, 32, 1, false);
  const hullMat = new THREE.MeshPhongMaterial({
    color: 0x60a5fa,
    transparent: true,
    opacity: 0.12,
    side: THREE.DoubleSide,
    shininess: 80,
  });
  const hull = new THREE.Mesh(hullGeom, hullMat);
  hull.position.set(center[0], center[1], center[2]);
  hull.rotation.z = Math.PI / 2;
  scene.add(hull);

  const hullEdges = new THREE.EdgesGeometry(hullGeom);
  const hullLine = new THREE.LineSegments(hullEdges, new THREE.LineBasicMaterial({
    color: 0x60a5fa,
    transparent: true,
    opacity: 0.4,
  }));
  hullLine.position.copy(hull.position);
  hullLine.rotation.copy(hull.rotation);
  scene.add(hullLine);

  const colorMap = {
    point: 0x3b82f6,
    line: 0x22c55e,
    surface: 0xf59e0b,
    volume: 0xec4899,
  };
  const ptColor = colorMap[sourceType] || 0x3b82f6;
  const maxW = Math.max(...weights);
  const minW = Math.min(...weights);

  const sphereGroup = new THREE.Group();
  elements.forEach((pt, i) => {
    const wi = weights[i];
    const norm = maxW > minW ? (wi - minW) / (maxW - minW) : 0.5;
    const radius = Math.max(0.3, 0.4 + norm * 2.0);

    const sg = new THREE.SphereGeometry(radius, 12, 12);
    const sm = new THREE.MeshPhongMaterial({
      color: ptColor,
      emissive: ptColor,
      emissiveIntensity: 0.4,
      transparent: true,
      opacity: 0.88,
    });
    const sphere = new THREE.Mesh(sg, sm);
    sphere.position.set(pt[0], pt[1], pt[2]);
    sphereGroup.add(sphere);
  });
  scene.add(sphereGroup);

  const rcGeom = new THREE.ConeGeometry(Math.max(viewSize * 0.02, 1), Math.max(viewSize * 0.04, 2), 16);
  const rcMat = new THREE.MeshPhongMaterial({
    color: 0xef4444,
    emissive: 0xef4444,
    emissiveIntensity: 0.6,
  });
  const rcMesh = new THREE.Mesh(rcGeom, rcMat);
  rcMesh.position.set(receiver[0], receiver[1], receiver[2]);
  scene.add(rcMesh);

  const rcRing = new THREE.Mesh(
    new THREE.TorusGeometry(Math.max(viewSize * 0.03, 1.5), 0.2, 8, 32),
    new THREE.MeshBasicMaterial({ color: 0xef4444, transparent: true, opacity: 0.6 })
  );
  rcRing.position.copy(rcMesh.position);
  rcRing.rotation.x = Math.PI / 2;
  scene.add(rcRing);

  const propPts = [
    new THREE.Vector3(center[0], center[1], center[2]),
    new THREE.Vector3(receiver[0], receiver[1], receiver[2]),
  ];
  const propGeom = new THREE.BufferGeometry().setFromPoints(propPts);
  const propMat = new THREE.LineDashedMaterial({
    color: 0x94a3b8,
    dashSize: Math.max(viewSize * 0.02, 2),
    gapSize: Math.max(viewSize * 0.01, 1),
    transparent: true,
    opacity: 0.5,
  });
  const propLine = new THREE.Line(propGeom, propMat);
  propLine.computeLineDistances();
  scene.add(propLine);

  const fontSize = Math.max(viewSize * 0.025, 3);
  const textCanvas = document.createElement("canvas");
  const ctx = textCanvas.getContext("2d");
  textCanvas.width = 256;
  textCanvas.height = 64;
  function makeLabel(text, color) {
    ctx.clearRect(0, 0, 256, 64);
    ctx.font = "bold 28px sans-serif";
    ctx.fillStyle = color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 128, 32);
    const tex = new THREE.CanvasTexture(textCanvas);
    const sprMat = new THREE.SpriteMaterial({ map: tex, transparent: true });
    const spr = new THREE.Sprite(sprMat);
    spr.scale.set(fontSize * 4, fontSize, 1);
    return spr;
  }

  const srcLabel = makeLabel("UUV", "#60a5fa");
  srcLabel.position.set(center[0], center[1] + hullRadius + fontSize, center[2]);
  scene.add(srcLabel);

  const rxLabel = makeLabel("RX", "#f87171");
  rxLabel.position.set(receiver[0], receiver[1] + fontSize * 1.5, receiver[2]);
  scene.add(rxLabel);

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  function onResize() {
    const r = container.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      camera.aspect = r.width / r.height;
      camera.updateProjectionMatrix();
      renderer.setSize(r.width, r.height);
    }
  }
  window.addEventListener("resize", onResize);

  geometry3D = { renderer, scene, camera, controls, onResize };

  const typeLabel = sourceLabels[sourceType] || sourceType;
  const info = $("geometry3dInfo");
  if (info) {
    info.innerHTML = `
      <span class="geom-tag ${sourceType}">${typeLabel}</span>
      <span>${geom.num_elements} 个离散单元</span>
      <span>艇长 ${length}m × 直径 ${diameter}m</span>
      <span>接收距离 ${range.toFixed(1)} m</span>
      <span class="geom-tag receiver">接收器 (${receiver.map((v) => v.toFixed(1)).join(", ")})</span>
      <span style="color:#64748b;flex:1;text-align:right">鼠标拖拽旋转 · 滚轮缩放 · 右键平移</span>
    `;
  }
}

function fillResultPanes(job) {
  const cases = job && Array.isArray(job.cases) ? job.cases : [];
  const firstCase = cases[0];

  state.currentCase = firstCase || null;

  const paneMap = {
    overview: firstCase ? renderOverview(firstCase) : emptyHtml("等待仿真开始", "配置左侧参数后，点击\"开始仿真\"按钮运行计算"),
    spectrum: firstCase ? renderImagePane(firstCase, "spectrum_png") : `<div class="empty-state simple"><h4>源级谱</h4><p>运行仿真后展示频谱分析结果</p></div>`,
    waveform: firstCase ? renderImagePane(firstCase, "waveforms_png") : `<div class="empty-state simple"><h4>时域波形</h4><p>运行仿真后展示时域波形图</p></div>`,
    lofar: firstCase && firstCase.module !== "environment" ? renderImagePane(firstCase, "lofar_png") : firstCase ? renderImagePane(firstCase, "spectrogram_png") : `<div class="empty-state simple"><h4>LOFAR / 谱图</h4><p>运行仿真后展示时频分析结果</p></div>`,
    demon: firstCase && firstCase.module !== "environment" ? renderImagePane(firstCase, "demon_png") : firstCase ? `<div class="empty-state simple"><h4>DEMON 不适用于背景噪声</h4><p>环境背景噪声请查看频谱、波形和时频谱</p></div>` : `<div class="empty-state simple"><h4>DEMON 谱</h4><p>运行仿真后展示 DEMON 分析结果</p></div>`,
    audio: firstCase ? renderAudioPane(firstCase) : `<div class="empty-state simple"><h4>音频试听</h4><p>运行仿真后可试听生成的噪声信号</p></div>`,
    files: firstCase ? renderFilesPane(firstCase) : `<div class="empty-state simple"><h4>输出文件</h4><p>运行仿真后可下载各类输出文件</p></div>`,
  };

  document.querySelectorAll(".result-pane").forEach((pane) => {
    const key = pane.dataset.pane;
    if (key === "geometry") return;
    if (paneMap[key] !== undefined) {
      pane.innerHTML = paneMap[key];
    }
  });

  if (firstCase && firstCase.geometry && firstCase.geometry.element_xyz_m) {
    const geomPane = document.querySelector('.result-pane[data-pane="geometry"]');
    if (geomPane) {
      geomPane.innerHTML = `
        <div class="geometry3d-wrap">
          <div id="geometry3dContainer" class="geometry3d-container"></div>
          <div class="geometry3d-info" id="geometry3dInfo">
            <span>正在加载三维场景...</span>
          </div>
        </div>
      `;
    }
    if (state.selectedResultTab === "geometry") {
      requestAnimationFrame(() => renderGeometry3D(firstCase));
    }
  } else {
    const geomPane = document.querySelector('.result-pane[data-pane="geometry"]');
    if (geomPane) {
      geomPane.innerHTML = firstCase && firstCase.module === "environment"
        ? `<div class="empty-state simple"><h4>环境噪声无目标几何</h4><p>背景噪声模块输出分量频谱、波形、时频谱和音频</p></div>`
        : `<div class="empty-state simple"><h4>三维等效源几何</h4><p>运行仿真后展示几何分布</p></div>`;
    }
  }

  // Update meta
  $("resultMeta").textContent = job
    ? `${job.job_id} · ${statusLabel(job.status)}`
    : "等待运行";

  // Update run badge
  const rb = $("runBadge");
  rb.className = "run-badge";
  if (!job) {
    rb.textContent = "未运行";
  } else if (job.status === "succeeded") {
    rb.classList.add("ok");
    rb.textContent = "仿真完成";
  } else if (job.status === "running") {
    rb.classList.add("running");
    rb.textContent = "运行中";
  } else if (job.status === "failed") {
    rb.classList.add("bad");
    rb.textContent = "仿真失败";
  } else {
    rb.textContent = job.message || "等待运行";
  }
}

/* ===== Job Operations ===== */
function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function pollJob(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    const job = await res.json();
    state.currentJob = job;
    fillResultPanes(job);
    if (job.status === "succeeded" || job.status === "failed") {
      stopPolling();
      $("runButton").disabled = false;
    }
  } catch (e) {
    $("runBadge").textContent = String(e);
  }
}

async function startJob(e) {
  e.preventDefault();
  stopPolling();
  $("runButton").disabled = true;

  const rb = $("runBadge");
  rb.className = "run-badge running";
  rb.textContent = "提交中...";

  // Show running state
  const overviewPane = document.querySelector('.result-pane[data-pane="overview"]');
  overviewPane.innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">
        <svg viewBox="0 0 120 120" fill="none">
          <circle cx="60" cy="60" r="48" stroke="currentColor" stroke-width="1.5" opacity="0.2">
            <animate attributeName="r" values="30;48;30" dur="2s" repeatCount="indefinite"/>
          </circle>
          <circle cx="60" cy="60" r="34" stroke="currentColor" stroke-width="1.5" opacity="0.25">
            <animate attributeName="r" values="20;34;20" dur="2s" repeatCount="indefinite" begin="0.3s"/>
          </circle>
          <circle cx="60" cy="60" r="20" fill="currentColor" opacity="0.15">
            <animate attributeName="r" values="10;20;10" dur="2s" repeatCount="indefinite" begin="0.6s"/>
          </circle>
        </svg>
      </div>
      <h3>仿真运行中</h3>
      <p>后端正在运行 ${state.selectedModule === "environment" ? "海洋背景噪声" : sourceLabels[state.selectedSource]} 仿真，请稍候...</p>
    </div>
  `;
  setResultTab("overview");

  try {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectConfig()),
    });
    const job = await res.json();
    state.currentJob = job;
    fillResultPanes(job);
    state.pollTimer = setInterval(() => pollJob(job.job_id), 2000);
    pollJob(job.job_id);
  } catch (e) {
    $("runButton").disabled = false;
    rb.className = "run-badge bad";
    rb.textContent = "提交失败";
    overviewPane.innerHTML = `<div class="empty-state"><h3>提交失败</h3><p>${String(e)}</p></div>`;
  }
}

/* ===== Init ===== */
function init() {
  bindModuleButtons();
  bindSourceButtons();
  bindResultTabs();
  setModule("target");

  $("simForm").addEventListener("submit", startJob);
  $("refreshBtn").addEventListener("click", () => {
    checkHealth();
  });

  checkHealth();
}

init();
