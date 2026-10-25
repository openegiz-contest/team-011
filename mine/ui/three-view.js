// 3D view of the pit. Reads the same twin state as the 2D map (things, trucks,
// drone, geometry, truckPos from app.js) and renders it with three.js.
import * as THREE from "three";
import { OrbitControls } from "./vendor/three/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "./vendor/three/CSS2DRenderer.js";

const VZ = 1.4;
const EQ = 2.5;
const BENCH = 15;
const ROAD_W = 42;
const SKY = "#c9cfd2";
const v3 = (x, y, z = 0) => new THREE.Vector3(x, z * VZ, -y);
const lambert = (color, extra = {}) => new THREE.MeshLambertMaterial({ color, ...extra });

function hash(i, j) {
  const s = Math.sin(i * 127.1 + j * 311.7) * 43758.5453;
  return s - Math.floor(s);
}
function vnoise(x, y) {
  const i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  const a = hash(i, j) + (hash(i + 1, j) - hash(i, j)) * u;
  const b = hash(i, j + 1) + (hash(i + 1, j + 1) - hash(i, j + 1)) * u;
  return a + (b - a) * v;
}
const fbm = (x, y) => 0.5 * vnoise(x / 420, y / 420) + 0.3 * vnoise(x / 160 + 7, y / 160) + 0.2 * vnoise(x / 55, y / 55 + 3);
const noise = (x, y) => (fbm(x, y) - 0.5) * 9;

function box(w, h, d, mat, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y, z);
  return m;
}

function makeLabel(text, cls = "") {
  const el = document.createElement("div");
  el.className = `lbl ${cls}`;
  el.textContent = text;
  return new CSS2DObject(el);
}

function setText(obj, text, color) {
  if (obj.element.textContent !== text) obj.element.textContent = text;
  if (color !== undefined) obj.element.style.color = color;
}

// --- terrain -------------------------------------------------------------------
function buildGround(g) {
  const { a_m: a, b_m: b, wall_slope: ws, benches_z } = g.pit;
  const floorD = -Math.min(...benches_z);

  const pitDepth = (x, y) => {
    if ((x / a) ** 2 + (y / b) ** 2 >= 1) return 0;
    const inside = (d) => (x / (a - d * ws)) ** 2 + (y / (b - d * ws)) ** 2 <= 1;
    if (inside(floorD)) return floorD;
    let lo = 0, hi = floorD;
    for (let i = 0; i < 24; i++) { const m = (lo + hi) / 2; if (inside(m)) lo = m; else hi = m; }
    return lo;
  };
  const stair = (d) => {
    if (d >= floorD) return -floorD;
    const k = Math.floor(d / BENCH), f = d / BENCH - k;
    return -(k * BENCH + BENCH * Math.min(1, Math.max(0, (f - 0.45) / 0.55)));
  };
  const natural = (x, y) => {
    const d = pitDepth(x, y);
    if (d > 0) return stair(d);
    const r = Math.sqrt((x / a) ** 2 + (y / b) ** 2);
    const rim = Math.max(0, 1 - Math.abs(r - 1.03) / 0.03) * 2.5;
    return noise(x, y) * Math.min(1, (r - 1) * 6) + rim;
  };

  const CELL = 120, buckets = new Map();
  const key = (i, j) => `${i},${j}`;
  const parkRow = g.bays?.length ? [[g.bays[0], g.bays[g.bays.length - 1]]] : [];
  for (const pts of [...Object.values(g.routes), ...Object.values(g.roads || {}), ...parkRow]) {
    for (let i = 1; i < pts.length; i++) {
      const s = { ax: pts[i - 1][0], ay: pts[i - 1][1], az: pts[i - 1][2] || 0, bx: pts[i][0], by: pts[i][1], bz: pts[i][2] || 0 };
      const i0 = Math.floor((Math.min(s.ax, s.bx) - 60) / CELL), i1 = Math.floor((Math.max(s.ax, s.bx) + 60) / CELL);
      const j0 = Math.floor((Math.min(s.ay, s.by) - 60) / CELL), j1 = Math.floor((Math.max(s.ay, s.by) + 60) / CELL);
      for (let ii = i0; ii <= i1; ii++) for (let jj = j0; jj <= j1; jj++) {
        const k = key(ii, jj);
        if (!buckets.has(k)) buckets.set(k, []);
        buckets.get(k).push(s);
      }
    }
  }
  const nearestRoad = (x, y) => {
    const list = buckets.get(key(Math.floor(x / CELL), Math.floor(y / CELL)));
    if (!list) return null;
    let best = null;
    for (const s of list) {
      const dx = s.bx - s.ax, dy = s.by - s.ay, len2 = dx * dx + dy * dy;
      const u = len2 ? Math.max(0, Math.min(1, ((x - s.ax) * dx + (y - s.ay) * dy) / len2)) : 0;
      const d = Math.hypot(s.ax + u * dx - x, s.ay + u * dy - y);
      if (!best || d < best.d) best = { d, z: s.az + u * (s.bz - s.az) };
    }
    return best;
  };

  // Every road cuts its bed into the ground with 1:1.3 side walls; the cut is
  // the lowest over all nearby roads so a ramp below a surface road wins.
  // On the pit surface (`fill`) a road above the slope also gets an embankment,
  // never higher than the cut of any road nearby.
  const roadCut = (x, y, h, fill = false) => {
    const list = buckets.get(key(Math.floor(x / CELL), Math.floor(y / CELL)));
    let lo = -Infinity, hi = Infinity;
    for (const s of list || []) {
      const dx = s.bx - s.ax, dy = s.by - s.ay, len2 = dx * dx + dy * dy;
      const u = len2 ? Math.max(0, Math.min(1, ((x - s.ax) * dx + (y - s.ay) * dy) / len2)) : 0;
      const d = Math.hypot(s.ax + u * dx - x, s.ay + u * dy - y);
      if (d >= 60) continue;
      const z = s.az + u * (s.bz - s.az) - 0.5, k = Math.max(0, d - ROAD_W / 2 - 1) * 1.3;
      hi = Math.min(hi, z + k);
      lo = Math.max(lo, z - k);
    }
    h = Math.min(h, hi);
    return fill ? Math.max(h, Math.min(lo, hi)) : h;
  };

  const ore = Object.values(g.faces).find((f) => f.material === "ore");
  const oreAt = (x, y) => {
    if (!ore) return 0;
    const cx = ore.position[0] + 40, cy = ore.position[1] - 30, c = Math.cos(0.35), s = Math.sin(0.35);
    const u = ((x - cx) * c + (y - cy) * s) / 260, v = (-(x - cx) * s + (y - cy) * c) / 150;
    return Math.max(0, 1 - (u * u + v * v)) ** 0.5;
  };

  const pitColor = (x, y, d, face) => {
    const depth = d / floorD;
    let [r, gg, bb] = face ? [0.56, 0.50, 0.42] : [0.68, 0.62, 0.52];
    const n = d >= floorD ? 0 : (vnoise(x / 45, y / 45) - 0.5) * 0.10 + (vnoise(x / 9, y / 9) - 0.5) * 0.05;
    const dim = 1 - 0.16 * depth + n;
    r *= dim; gg *= dim; bb *= dim * 0.98;
    const o = oreAt(x, y) * 0.75;
    return [r + (0.64 - r) * o, gg + (0.38 - gg) * o, bb + (0.17 - bb) * o];
  };

  // Working pad at each face: a level platform at face height, cut into the
  // wall above and (on the pit surface) built up over the slope below, so the
  // shovel and the trucks beside it stand on the floor, not inside the wall.
  // The face moves along its cut during the shift, so the pad covers the whole cut.
  const PAD = 60;
  const pads = Object.entries(g.faces).flatMap(([f, face]) => face.cut || [facePos(f)]);
  const padCut = (x, y, h, fill = false) => {
    let lo = -Infinity, hi = Infinity;
    for (const [fx, fy, fz] of pads) {
      const d = Math.hypot(x - fx, y - fy);
      if (d >= PAD + 80) continue;
      const k = Math.max(0, d - PAD) * 1.3;
      hi = Math.min(hi, fz + k);
      lo = Math.max(lo, fz - k);
    }
    h = Math.min(h, hi);
    return fill ? Math.max(h, Math.min(lo, hi)) : h;
  };

  const rings = [];
  for (let k = 0; k * BENCH < floorD; k++) {
    const d0 = k * BENCH;
    rings.push([d0, false], [d0 + 3.4, false], [d0 + 6.75, false], [d0 + 6.75, true], [d0 + 10.9, true],
      [Math.min(floorD, d0 + BENCH), true]);
  }
  rings.push([floorD, false]);
  const SEG = 512;
  const pp = [], pc = [], pi = [];
  const col = new THREE.Color();
  for (const [d, face] of rings) {
    for (let s = 0; s <= SEG; s++) {
      const th = (s / SEG) * Math.PI * 2;
      const x = (a - d * ws) * Math.cos(th), y = (b - d * ws) * Math.sin(th);
      let h = stair(d);
      h = roadCut(x, y, padCut(x, y, h, d < floorD), d < floorD);
      pp.push(x, h * VZ, -y);
      col.setRGB(...pitColor(x, y, d, face), THREE.SRGBColorSpace);
      pc.push(col.r, col.g, col.b);
    }
  }
  const W = SEG + 1;
  for (let r = 0; r < rings.length - 1; r++) {
    for (let s = 0; s < SEG; s++) {
      const p = r * W + s, q = (r + 1) * W + s;
      pi.push(p, q, p + 1, p + 1, q, q + 1);
    }
  }
  const centre = pp.length / 3;
  col.setRGB(...pitColor(0, 0, floorD, false), THREE.SRGBColorSpace);
  pp.push(0, -floorD * VZ, 0);
  pc.push(col.r, col.g, col.b);
  const last = (rings.length - 1) * W;
  for (let s = 0; s < SEG; s++) pi.push(last + s, centre, last + s + 1);
  const pitGeo = new THREE.BufferGeometry();
  pitGeo.setAttribute("position", new THREE.Float32BufferAttribute(pp, 3));
  pitGeo.setAttribute("color", new THREE.Float32BufferAttribute(pc, 3));
  pitGeo.setIndex(pi);
  pitGeo.computeVertexNormals();
  const pitMesh = new THREE.Mesh(pitGeo, new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide }));

  const X0 = -1700, X1 = 2400, Y0 = -1100, Y1 = 1800, NX = 330, NY = 240;
  const geo = new THREE.PlaneGeometry(X1 - X0, Y1 - Y0, NX, NY);
  geo.rotateX(-Math.PI / 2);
  geo.translate((X0 + X1) / 2, 0, -(Y0 + Y1) / 2);
  const pos = geo.attributes.position;
  const colors = new Float32Array(pos.count * 3);
  const inPit = new Uint8Array(pos.count);
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), y = -pos.getZ(i);
    const d = pitDepth(x, y);
    let h = natural(x, y);
    let r, gg, bb;
    if ((x / a) ** 2 + (y / b) ** 2 < 1) {
      inPit[i] = 1;
      h = roadCut(x, y, stair(d)) - 14;
      [r, gg, bb] = pitColor(x, y, d, true);
    } else {
      const road = nearestRoad(x, y);
      const rw = road ? (road.d < 22 ? 1 : road.d < 40 ? 1 - (road.d - 22) / 18 : 0) : 0;
      if (rw) h += (road.z - h) * rw;
      h = roadCut(x, y, h);
      const n = (vnoise(x / 35, y / 35) - 0.5) * 0.05 + (fbm(x + 900, y) - 0.5) * 0.08;
      const green = Math.max(0, vnoise(x / 260 + 11, y / 260) - 0.55) * 0.12;
      r = 0.47 + n - green * 0.5; gg = 0.43 + n + green * 0.4; bb = 0.31 + n * 0.8 - green * 0.3;
      if (rw) { r += (0.50 - r) * rw; gg += (0.47 - gg) * rw; bb += (0.42 - bb) * rw; }
    }
    const cut = padCut(x, y, h);
    if (cut < h && !inPit[i]) { const k = Math.min(1, (h - cut) / 20); r *= 1 - 0.2 * k; gg *= 1 - 0.22 * k; bb *= 1 - 0.25 * k; }
    h = cut;
    pos.setY(i, h * VZ);
    col.setRGB(r, gg, bb, THREE.SRGBColorSpace);
    colors.set([col.r, col.g, col.b], i * 3);
  }
  const index = geo.index.array, kept = [];
  for (let t = 0; t < index.length; t += 3) {
    if (!(inPit[index[t]] && inPit[index[t + 1]] && inPit[index[t + 2]])) kept.push(index[t], index[t + 1], index[t + 2]);
  }
  geo.setIndex(kept);
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geo.computeVertexNormals();
  const mesh = new THREE.Group();
  mesh.add(new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ vertexColors: true })), pitMesh);
  return { mesh, height: (x, y) => natural(x, y) * VZ };
}

function ribbon(pts, width, color, lift = 0.8, skirt = 8, extra = {}) {
  const pos = [], idx = [];
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const p = pts[i], q = pts[Math.min(n - 1, i + 1)], o = pts[Math.max(0, i - 1)];
    let dx = q[0] - o[0], dy = q[1] - o[1];
    const L = Math.hypot(dx, dy) || 1;
    dx /= L; dy /= L;
    const nx = -dy * width / 2, ny = dx * width / 2;
    const z = (p[2] || 0) * VZ + lift;
    pos.push(p[0] + nx, z, -(p[1] + ny), p[0] - nx, z, -(p[1] - ny),
      p[0] + nx * 1.2, z - skirt, -(p[1] + ny * 1.2), p[0] - nx * 1.2, z - skirt, -(p[1] - ny * 1.2));
  }
  for (let i = 0; i < n - 1; i++) {
    const a = i * 4, b = (i + 1) * 4;
    idx.push(a, b, a + 1, a + 1, b, b + 1);
    if (skirt) idx.push(a + 2, b + 2, a, a, b + 2, b, a + 1, b + 1, a + 3, a + 3, b + 1, b + 3);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, lambert(color, { side: THREE.DoubleSide, ...extra }));
  mesh.receiveShadow = true;
  return mesh;
}

function centerLine(pts, lift = 1.3) {
  const geo = new THREE.BufferGeometry().setFromPoints(pts.map((p) => v3(p[0], p[1], p[2] || 0).setY((p[2] || 0) * VZ + lift)));
  const line = new THREE.Line(geo, new THREE.LineDashedMaterial({ color: "#d8cfb8", dashSize: 9, gapSize: 12, transparent: true, opacity: 0.5 }));
  line.computeLineDistances();
  return line;
}

function pile(x, y, base, rx, ry, colors, tier = 12) {
  const g = new THREE.Group();
  colors.forEach((c, i) => {
    const k = 1 - i * 0.22;
    const m = new THREE.Mesh(new THREE.CylinderGeometry(1 - 0.14 / k, 1, 1, 48), lambert(c));
    m.scale.set(rx * k, tier * VZ, ry * k);
    m.position.set(x - i * 6, base + (i + 0.5) * tier * VZ, -(y + i * 6));
    g.add(m);
  });
  return g;
}

// --- equipment ------------------------------------------------------------------
// Shared geometry and materials: ten trucks share one set.
const TRUCK = (() => {
  const yellow = lambert("#e3a92b"), dark = lambert("#24262a"), steel = lambert("#8d8a83");
  const bedShape = new THREE.Shape();
  bedShape.moveTo(-5.2, 0); bedShape.lineTo(3.4, 0); bedShape.lineTo(3.9, 3.6); bedShape.lineTo(-6.2, 2.2); bedShape.closePath();
  const bedGeo = new THREE.ExtrudeGeometry(bedShape, { depth: 7.2, bevelEnabled: false });
  bedGeo.translate(0, 0, -3.6);
  return {
    yellow, dark, steel,
    bed: bedGeo,
    tyre: new THREE.CylinderGeometry(1.9, 1.9, 1.6, 18),
    hub: new THREE.CylinderGeometry(0.9, 0.9, 1.7, 10),
    load: new THREE.CylinderGeometry(2.4, 3.6, 1.9, 6),
  };
})();

function makeTruck() {
  const g = new THREE.Group();
  g.add(box(12.5, 1.5, 4.2, TRUCK.dark, 0, 2.6, 0));
  const bed = new THREE.Mesh(TRUCK.bed, TRUCK.yellow);
  bed.position.set(-1.6, 3.4, 0);
  const load = new THREE.Mesh(TRUCK.load, lambert("#c47a35"));
  load.scale.set(1.55, 1, 1.05);
  load.position.set(-2.4, 6.0, 0);
  // Front deck: engine hood, upper deck, cab on the left side, ladder.
  const hood = box(2.6, 3.0, 4.4, TRUCK.yellow, 5.2, 4.3, 0);
  const deck = box(3.6, 0.5, 7.4, TRUCK.yellow, 4.6, 5.9, 0);
  const cab = box(2.4, 2.4, 2.6, lambert("#4ea1ff"), 4.4, 7.3, 2.2);
  const glass = box(0.2, 1.2, 2.2, lambert("#1b2a38"), 5.65, 7.6, 2.2);
  const grille = box(0.3, 2.4, 3.4, TRUCK.dark, 6.55, 4.2, 0);
  g.add(bed, load, hood, deck, cab, glass, grille);
  for (const [x, z, dual] of [[4.4, 3.0, false], [4.4, -3.0, false], [-3.4, 2.7, true], [-3.4, -2.7, true]]) {
    for (const off of dual ? [0, Math.sign(z) * 1.7] : [0]) {
      const w = new THREE.Mesh(TRUCK.tyre, TRUCK.dark);
      w.rotation.x = Math.PI / 2;
      w.position.set(x, 1.9, z + off);
      const h = new THREE.Mesh(TRUCK.hub, TRUCK.steel);
      h.rotation.x = Math.PI / 2;
      h.position.copy(w.position);
      g.add(w, h);
    }
  }
  g.traverse((o) => { o.castShadow = true; });
  g.scale.setScalar(EQ);
  g.userData = { cab, load, bed };
  return g;
}

function makeShovel() {
  const g = new THREE.Group();
  const dark = lambert("#24272c");
  g.add(box(15, 3.2, 4.2, dark, 0, 1.6, 5.4), box(15, 3.2, 4.2, dark, 0, 1.6, -5.4));
  const house = new THREE.Group();
  house.position.y = 3.2;
  const body = box(12, 7, 10, lambert("#d0d7de"), -1.5, 3.5, 0);
  const cabin = box(3.6, 3.4, 3.6, lambert("#e8e6df"), 3.2, 8.6, 3.0);
  const window_ = box(0.2, 1.6, 3.0, lambert("#1b2a38"), 5.05, 9.0, 3.0);
  house.add(body, cabin, window_, box(4, 5, 9, lambert("#3a3f47"), -8, 2.5, 0), box(10, 0.6, 8, lambert("#ff8a1f"), -1.5, 7.3, 0));
  const boom = box(17, 1.8, 1.8, lambert("#d9a21b"), 0, 0, 0);
  boom.position.set(10, 9, 0);
  boom.rotation.z = 0.55;
  const stick = box(1.6, 11, 1.6, lambert("#d9a21b"), 17.5, 8, 0);
  stick.rotation.z = -0.25;
  const bucket = box(4.5, 4, 5.5, lambert("#3a3f47"), 19, 2.4, 0);
  house.add(boom, stick, bucket);
  g.add(house);
  g.traverse((o) => { o.castShadow = true; });
  g.scale.setScalar(EQ);
  g.userData = { house, body };
  return g;
}

function makeDrone() {
  const g = new THREE.Group();
  const mat = lambert("#00e5ff", { emissive: "#006070" });
  g.add(box(2, 0.8, 2, mat));
  const arm = box(9, 0.3, 0.4, mat);
  arm.rotation.y = Math.PI / 4;
  const arm2 = arm.clone();
  arm2.rotation.y = -Math.PI / 4;
  g.add(arm, arm2);
  const rotors = [];
  for (const [x, z] of [[3.2, 3.2], [3.2, -3.2], [-3.2, 3.2], [-3.2, -3.2]]) {
    const r = new THREE.Mesh(new THREE.BoxGeometry(3.6, 0.1, 0.5), mat);
    r.position.set(x, 0.5, z);
    rotors.push(r);
    g.add(r);
  }
  g.scale.setScalar(4);
  g.userData = { rotors };
  return g;
}

function makeDozer() {
  const g = new THREE.Group();
  const dark = lambert("#24272c");
  g.add(box(9, 2.4, 2.2, dark, 0, 1.2, 2.6), box(9, 2.4, 2.2, dark, 0, 1.2, -2.6));
  const body = box(7, 3.4, 4.6, lambert("#e0b830"), -0.5, 3.6, 0);
  g.add(body, box(3, 2.6, 3.6, lambert("#3a3f47"), -1.5, 6.4, 0), box(1, 3.4, 8, lambert("#5c5a55"), 5.6, 1.9, 0));
  g.traverse((o) => { o.castShadow = true; });
  g.scale.setScalar(EQ);
  g.userData = { body };
  return g;
}

function makeGrader() {
  const g = new THREE.Group();
  const dark = lambert("#24272c");
  const body = box(14, 1.4, 2.6, lambert("#e8c94a"), 0, 3.2, 0);
  g.add(body, box(3.6, 3.4, 3.4, lambert("#e8c94a"), -4.5, 5.2, 0), box(0.6, 1.6, 6, lambert("#5c5a55"), 1.5, 1.2, 0));
  const wheel = new THREE.CylinderGeometry(1.4, 1.4, 1, 12);
  for (const [x, z] of [[6, 1.8], [6, -1.8], [-4, 1.8], [-4, -1.8], [-6.5, 1.8], [-6.5, -1.8]]) {
    const w = new THREE.Mesh(wheel, dark);
    w.rotation.x = Math.PI / 2;
    w.position.set(x, 1.4, z);
    g.add(w);
  }
  g.traverse((o) => { o.castShadow = true; });
  g.scale.setScalar(EQ);
  g.userData = { body };
  return g;
}

// --- view -----------------------------------------------------------------------
export function createView(container) {
  const g = geometry;
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.2;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  container.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  labels.domElement.className = "labels3d";
  container.appendChild(labels.domElement);
  const badge = document.createElement("div");
  badge.className = "follow3d";
  container.appendChild(badge);

  const scene = new THREE.Scene();
  const skyCanvas = document.createElement("canvas");
  skyCanvas.width = 4; skyCanvas.height = 256;
  const sg = skyCanvas.getContext("2d"), grad = sg.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, "#5b86b5"); grad.addColorStop(0.55, "#a9bfd3"); grad.addColorStop(1, SKY);
  sg.fillStyle = grad; sg.fillRect(0, 0, 4, 256);
  const skyTex = new THREE.CanvasTexture(skyCanvas);
  skyTex.colorSpace = THREE.SRGBColorSpace;
  scene.background = skyTex;
  scene.fog = new THREE.Fog(SKY, 2600, 7500);
  scene.add(new THREE.HemisphereLight("#dce8f5", "#7a6a50", 1.7));
  // The sun keeps its shadow box around the point the camera looks at.
  const SUN_DIR = new THREE.Vector3(-0.5, 0.78, 0.42).normalize();
  const sun = new THREE.DirectionalLight("#fff0d6", 2.6);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 1.5;
  Object.assign(sun.shadow.camera, { left: -900, right: 900, top: 900, bottom: -900, near: 10, far: 6000 });
  scene.add(sun, sun.target);
  const camera = new THREE.PerspectiveCamera(45, 1, 10, 20000);
  const home = { target: v3(250, 200, -80), pos: v3(1150, -1250, 1050) };
  camera.position.copy(home.pos);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(home.target);
  controls.enableDamping = true;
  controls.maxPolarAngle = 1.42;
  controls.minDistance = 60;
  controls.maxDistance = 7000;

  const ground = buildGround(g);
  ground.mesh.traverse((o) => { o.receiveShadow = true; });
  scene.add(ground.mesh);
  const shape = new THREE.Shape();
  shape.moveTo(-20000, -20000); shape.lineTo(20000, -20000); shape.lineTo(20000, 20000); shape.lineTo(-20000, 20000);
  const hole = new THREE.Path();
  hole.absellipse(0, 0, g.pit.a_m + 80, g.pit.b_m + 80, 0, Math.PI * 2, false, 0);
  shape.holes.push(hole);
  const outerGeo = new THREE.ShapeGeometry(shape, 64);
  outerGeo.rotateX(-Math.PI / 2);
  const outer = new THREE.Mesh(outerGeo, lambert("#8a7d5e"));
  outer.position.y = -9;
  scene.add(outer);

  const roadColor = "#6f685c";
  for (const [name, pts] of Object.entries(g.routes)) {
    if (name.endsWith("dump-2")) continue;
    scene.add(ribbon(pts, ROAD_W, roadColor));
    scene.add(centerLine(pts));
  }
  const overlays = {};
  for (const [name, pts] of Object.entries(g.roads || {})) {
    if (name === "road-dump-2") { scene.add(ribbon(pts, ROAD_W - 6, "#5a554c")); scene.add(centerLine(pts)); }
    if (name === "road-service") { scene.add(ribbon(pts, ROAD_W - 12, roadColor)); scene.add(centerLine(pts)); }
    const red = ribbon(pts, ROAD_W + 4, "#ff4a4a", 1.6, 0, { transparent: true, opacity: 0.85, emissive: "#401010" });
    const amber = ribbon(pts, 14, "#ffb020", 1.6, 0, { transparent: true, opacity: 0.7, emissive: "#402a00" });
    red.visible = amber.visible = false;
    const mid = pts[Math.floor(pts.length / 2)];
    const lbl = makeLabel("✕ дорога закрыта", "bad");
    lbl.position.copy(v3(mid[0], mid[1], mid[2] || 0)).setY(((mid[2] || 0) * VZ) + 40);
    lbl.visible = false;
    scene.add(red, amber, lbl);
    overlays[name] = { red, amber, lbl };
  }

  const pickables = [];
  const pick = (obj, name) => { obj.userData.twin = name; pickables.push(obj); return obj; };
  const at = (x, y) => ground.height(x, y);

  const [dx, dy] = g.waste_dump;
  scene.add(pick(pile(dx, dy, at(dx, dy) - 2, 170, 110, ["#5b5246", "#675d4f", "#746a5a", "#827765"], 16), "dump-01"));
  const dumpTop = at(dx, dy) - 2 + 4 * 16 * VZ;
  const freshDump = new THREE.Mesh(new THREE.CylinderGeometry(0.85, 1, 1, 40), lambert("#a1977f"));
  const toPit = Math.atan2(-dy, -dx);
  // Top tier of the pile (i = 3 in pile()): centre and flat-top half-axes.
  const top = { x: dx - 18, y: dy + 18, rx: 170 * 0.34 * (1 - 0.14 / 0.34), ry: 110 * 0.34 * (1 - 0.14 / 0.34) };
  freshDump.visible = false;
  scene.add(pick(freshDump, "dump-01"));
  const dumpLbl = makeLabel("Отвал вскрыши");
  dumpLbl.position.set(dx, at(dx, dy) + 120, -dy);
  scene.add(dumpLbl);

  let reserve = null, reserveLbl = null, shopLbl = null;
  if (g.reserve_dump) {
    const [rx, ry] = g.reserve_dump;
    reserve = pick(pile(rx, ry, at(rx, ry) - 2, 110, 75, ["#4a443a", "#554e43"], 14), "dump-02");
    scene.add(reserve);
    reserveLbl = makeLabel("Резервный отвал");
    reserveLbl.position.set(rx, at(rx, ry) + 50, -ry);
    scene.add(reserveLbl);
    if (g.bays?.length) {
      const b = g.bays, [ax, ay] = b[0], [bx, by] = b[b.length - 1];
      const L = Math.hypot(bx - ax, by - ay) || 1, ux = (bx - ax) / L, uy = (by - ay) / L;
      const z = 0;
      scene.add(ribbon([[ax - ux * 16, ay - uy * 16, z], [bx + ux * 16, by + uy * 16, z]], 44, "#57534b", 0.9, 4));
      const step = L / Math.max(1, b.length - 1);
      for (let i = 0; i <= b.length; i++) {
        const s = (i - 0.5) * step, cx = ax + ux * s, cy = ay + uy * s;
        scene.add(ribbon([[cx + uy * 18, cy - ux * 18, z], [cx - uy * 18, cy + ux * 18, z]], 0.8, "#d8cfb8", 1.1, 0));
      }
      const parkLbl = makeLabel("Стоянка");
      parkLbl.position.set((ax + bx) / 2, 30, -(ay + by) / 2);
      scene.add(parkLbl);
    }
    const [wx, wy] = g.workshop;
    const shop = new THREE.Group();
    shop.add(box(70, 20, 45, lambert("#4b4456"), 0, 6, 0), box(74, 3, 49, lambert("#e05cc0", { emissive: "#3a1030" }), 0, 17, 0));
    shop.position.set(wx, at(wx, wy), -wy);
    scene.add(pick(shop, "workshop-01"));
    shopLbl = makeLabel("Мастерская");
    shopLbl.position.set(wx, at(wx, wy) + 45, -wy);
    scene.add(shopLbl);
  }
  let fuelLbl = null;
  if (g.fuel) {
    const [sx0, sy0] = g.fuel, [fx, fy] = g.fuel_station || [sx0, sy0 + 55], fz = at(fx, fy);
    const station = new THREE.Group();
    station.add(box(50, 1, 26, lambert("#57534b"), 0, 0.5, 0));
    station.add(box(54, 2, 30, lambert("#2bb3a3", { emissive: "#0a302c" }), 0, 16, 0));
    for (const x of [-22, 22]) station.add(box(2, 15, 2, lambert("#9aa0a6"), x, 8, 0));
    for (const x of [-10, 10]) station.add(box(3, 6, 3, lambert("#d0d7de"), x, 3, 0));
    const tank = new THREE.Mesh(new THREE.CylinderGeometry(6, 6, 30, 16), lambert("#d0d7de"));
    tank.rotation.z = Math.PI / 2;
    tank.position.set(0, 6, 24);
    station.add(tank);
    station.rotation.y = Math.atan2(fy - sy0, fx - sx0) + Math.PI / 2;
    station.position.set(fx, fz, -fy);
    scene.add(pick(station, "fuel-01"));
    fuelLbl = makeLabel("Заправка", "small");
    fuelLbl.position.set(fx, fz + 40, -fy);
    scene.add(fuelLbl);
  }

  const [cx, cy] = g.crusher;
  const cBase = at(cx, cy);
  const crusher = new THREE.Group();
  crusher.add(box(160, 4, 110, lambert("#4a4740"), 0, 0, 0));
  const cBody = box(45, 34, 45, lambert("#c9a227"), 0, 17, 0);
  const hopper = new THREE.Mesh(new THREE.CylinderGeometry(30, 12, 16, 4), lambert("#5c5a55"));
  hopper.rotation.y = Math.PI / 4;
  hopper.position.set(-30, 12, 0);
  crusher.add(cBody, hopper);
  crusher.position.set(cx, cBase, -cy);
  scene.add(pick(crusher, "crusher-01"));
  const [sx, sy] = g.stockpile || [cx + 260, cy + 80];
  const sBase = at(sx, sy);
  const stock = pick(new THREE.Mesh(new THREE.ConeGeometry(75, 40 * VZ, 36), lambert("#b8733a")), "stockpile-01");
  stock.position.set(sx, sBase + 20 * VZ, -sy);
  scene.add(stock);
  const conveyorBetween = (a, b) => {
    const belt = b.clone().sub(a);
    const c = box(belt.length(), 3, 5, lambert("#9aa0a6"));
    c.position.copy(a).add(belt.clone().multiplyScalar(0.5));
    c.quaternion.setFromUnitVectors(new THREE.Vector3(1, 0, 0), belt.clone().normalize());
    scene.add(c);
  };
  conveyorBetween(new THREE.Vector3(cx, cBase + 20, -cy), new THREE.Vector3(sx, sBase + 28 * VZ, -sy));
  const stockLbl = makeLabel("Склад руды", "small");
  stockLbl.position.set(sx, sBase + 85, -sy);
  scene.add(stockLbl);

  let plantRoof = null, plantLbl = null;
  if (g.plant) {
    const [px, py] = g.plant;
    const pBase = at(px, py);
    const plant = new THREE.Group();
    plant.add(box(170, 4, 120, lambert("#4a4740"), 0, 0, 0));
    plant.add(box(140, 34, 90, lambert("#8e959c"), 0, 17, 0));
    plantRoof = box(146, 4, 96, lambert("#3ccf7a", { emissive: "#0c3020" }), 0, 36, 0);
    plant.add(plantRoof);
    for (const [ox, r] of [[-35, 16], [25, 13]]) {
      const mill = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 34, 24), lambert("#c9a227"));
      mill.rotation.x = Math.PI / 2;
      mill.position.set(ox, 54, 0);
      plant.add(mill);
    }
    plant.add(box(10, 70, 10, lambert("#6b7078"), 60, 35, 35));
    plant.position.set(px, pBase, -py);
    scene.add(pick(plant, "plant-01"));
    conveyorBetween(new THREE.Vector3(sx, sBase + 4, -sy), new THREE.Vector3(px - 70, pBase + 30, -py));
    plantLbl = makeLabel("Обогатительная фабрика");
    plantLbl.position.set(px, pBase + 110, -py);
    scene.add(plantLbl);
  }
  const crusherLbl = makeLabel("Дробилка Metso 60-89");
  crusherLbl.position.set(cx, cBase + 75, -cy);
  scene.add(crusherLbl);

  const benches = g.pit.benches_z;
  benches.forEach((z, i) => {
    const shrink = -z * g.pit.wall_slope, th = Math.PI * (1.08 + 0.1 * i);
    const l = makeLabel(`уступ ${z} м`, "dim");
    l.position.copy(v3((g.pit.a_m - shrink) * Math.cos(th), (g.pit.b_m - shrink) * Math.sin(th), z)).setY(z * VZ + 25);
    scene.add(l);
  });

  const shovels = {};
  const SHOVEL_AHEAD = 45;
  const rnd = (() => { let s = 11; return () => ((s = (s * 16807) % 2147483647) / 2147483647); })();
  for (const [face, f] of Object.entries(g.faces)) {
    const [fx, fy, fz] = facePos(face);
    const fg = new THREE.Group();
    fg.position.copy(v3(fx, fy, fz));
    scene.add(fg);
    const m = makeShovel();
    fg.add(pick(m, f.shovel));
    const muck = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(1, 0), lambert(f.material === "ore" ? "#a8642e" : "#8d877b"), 40);
    const mtx = new THREE.Matrix4();
    for (let i = 0; i < 40; i++) {
      const a = (rnd() - 0.5) * 1.1, r = 55 + rnd() * 45, s = 5 + rnd() * 8;
      mtx.compose(v3(Math.cos(a) * r, Math.sin(a) * r, 0).setY(s * 0.4),
        new THREE.Quaternion().setFromEuler(new THREE.Euler(rnd() * 3, rnd() * 3, rnd() * 3)), new THREE.Vector3(s, s * 0.7, s));
      muck.setMatrixAt(i, mtx);
    }
    fg.add(muck);
    const dome = new THREE.Mesh(new THREE.SphereGeometry(170, 40, 16, 0, Math.PI * 2, 0, Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: "#ff4a4a", transparent: true, opacity: 0.16, depthWrite: false }));
    dome.position.set(0, 0, 0);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(170, 1.5, 6, 80),
      new THREE.MeshBasicMaterial({ color: "#ff6060", transparent: true, opacity: 0.8 }));
    ring.rotation.x = Math.PI / 2;
    ring.position.copy(dome.position).setY(dome.position.y + 2);
    const gas = new THREE.Group();
    for (let i = 0; i < 9; i++) {
      const puff = new THREE.Mesh(new THREE.SphereGeometry(40 + rnd() * 40, 16, 10),
        new THREE.MeshBasicMaterial({ color: "#c8b27a", transparent: true, opacity: 0.12, depthWrite: false }));
      puff.position.set((rnd() - 0.5) * 180, 30 + rnd() * 70, (rnd() - 0.5) * 180);
      gas.add(puff);
    }
    gas.position.copy(dome.position);
    const lbl = makeLabel(f.shovel);
    lbl.position.copy(dome.position).setY(dome.position.y + 75);
    const zoneLbl = makeLabel("", "bad");
    zoneLbl.position.copy(dome.position).setY(dome.position.y + 190);
    fg.add(dome, ring, gas, lbl, zoneLbl);
    shovels[face] = { m, f, fg, muck, dome, ring, gas, lbl, zoneLbl, swing: 0 };
    placeShovel(shovels[face], fx, fy);
  }

  // The truck loads at the face point at the end of the road; the shovel works
  // ahead of it along the cut, facing the blasted rock further ahead, and
  // swings back to the truck.
  function placeShovel(s, fx, fy) {
    const c = s.f.cut;
    let dir = null;
    if (c?.length > 1) {
      let i = 0, best = Infinity;
      c.forEach((p, k) => { const d = Math.hypot(p[0] - fx, p[1] - fy); if (d < best) { best = d; i = k; } });
      const a = c[Math.min(i, c.length - 2)], b = c[Math.min(i, c.length - 2) + 1];
      const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
      dir = [(b[0] - a[0]) / L, (b[1] - a[1]) / L];
    }
    const out = Math.atan2(fy, fx);
    const [dx, dy] = dir ? [dir[0] * SHOVEL_AHEAD, dir[1] * SHOVEL_AHEAD] : [Math.cos(out) * 20, Math.sin(out) * 20];
    const facing = Math.atan2(dy, dx);
    s.m.position.copy(v3(dx, dy, 0));
    s.m.rotation.y = facing;
    s.muck.rotation.y = facing;
    const back = Math.atan2(-dy, -dx) - facing;
    s.swing = back - 2 * Math.PI * Math.round(back / (2 * Math.PI));
  }

  for (const obj of pickables) obj.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  const truckMeshes = {};
  const supportMeshes = { "dozer-01": pick(makeDozer(), "dozer-01"), "grader-01": pick(makeGrader(), "grader-01") };
  Object.values(supportMeshes).forEach((m) => { m.visible = false; scene.add(m); });
  const droneMesh = pick(makeDrone(), "drone-01");
  droneMesh.visible = false;
  scene.add(droneMesh);

  const RAIN = 5000;
  const rainGeo = new THREE.BufferGeometry();
  const rainPos = new Float32Array(RAIN * 3);
  for (let i = 0; i < RAIN; i++) rainPos.set([(Math.random() - 0.5) * 3000, Math.random() * 900, (Math.random() - 0.5) * 3000], i * 3);
  rainGeo.setAttribute("position", new THREE.BufferAttribute(rainPos, 3));
  const rain = new THREE.Points(rainGeo, new THREE.PointsMaterial({ color: "#a9c8ff", size: 3, transparent: true, opacity: 0.6 }));
  rain.visible = false;
  scene.add(rain);

  // --- interaction ---
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  let follow = null, down = null;
  const twinAt = (e) => {
    const r = renderer.domElement.getBoundingClientRect();
    mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(pickables, true).find((h) => h.object.visible);
    let o = hit?.object;
    while (o && !o.userData.twin) o = o.parent;
    return o?.userData.twin || null;
  };
  renderer.domElement.addEventListener("pointermove", (e) => {
    if (down) return;
    const r = renderer.domElement.getBoundingClientRect();
    tip.x = e.clientX - r.left; tip.y = e.clientY - r.top;
    hover = twinAt(e);
    renderer.domElement.style.cursor = hover ? "pointer" : "";
    if (hover) showTip(); else document.getElementById("tip").style.display = "none";
  });
  renderer.domElement.addEventListener("pointerleave", () => { hover = null; document.getElementById("tip").style.display = "none"; });
  renderer.domElement.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
  renderer.domElement.addEventListener("pointerup", (e) => {
    const click = down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 4;
    down = null;
    if (!click) return;
    const name = twinAt(e);
    follow = name && (name.startsWith("truck-") || name === "drone-01") ? name : null;
  });
  renderer.domElement.addEventListener("dblclick", () => {
    follow = null;
    controls.target.copy(home.target);
    camera.position.copy(home.pos);
  });

  // --- per-frame update ---
  const tmp = new THREE.Vector3();
  let lastText = 0;
  function update(now) {
    const pit = twin("pit-01");
    for (const [name, tr] of Object.entries(trucks)) {
      let m = truckMeshes[name];
      if (!m) {
        m = truckMeshes[name] = pick(makeTruck(), name);
        m.userData.lbl = makeLabel(name.replace("truck-", ""), "small");
        m.userData.lbl.position.set(0, 13, 0);
        m.add(m.userData.lbl);
        scene.add(m);
      }
      const p = truckDisplay(name, tr, now);
      m.position.copy(v3(p.x, p.y, p.z || 0));
      m.rotation.y = -p.heading;
      m.userData.cab.material.color.set(COLORS[tr.state] || "#8892a6");
      const loaded = (tr.payload || 0) > 1;
      m.userData.load.visible = loaded;
      if (loaded) m.userData.load.material.color.set(LOAD_COLORS[g.faces[tr.face]?.material] || LOAD_COLORS.waste);
      m.scale.setScalar(hover === name ? EQ * 1.25 : EQ);
    }

    const blastMode = val(twin("control-01"), "blast_mode");
    for (const [face, s] of Object.entries(shovels)) {
      const [fx, fy, fz] = facePos(face);
      const sh = twin(s.f.shovel);
      const sx = val(sh, "x"), sy = val(sh, "y"), sz = val(sh, "z");
      const px = sx != null ? sx : fx, py = sy != null ? sy : fy, pz = sz != null ? sz : fz;
      if (!s.spurAt || Math.hypot(fx - s.spurAt[0], fy - s.spurAt[1]) > 8) {
        s.spurAt = [fx, fy];
        if (s.spur) { scene.remove(s.spur); s.spur.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); }); s.spur = null; }
        const pts = faceSpur(face);
        if (pts) {
          s.spur = new THREE.Group();
          s.spur.add(ribbon(pts, ROAD_W - 8, roadColor), centerLine(pts));
          scene.add(s.spur);
        }
      }
      // Zone dome/muck stay on the face; only the shovel mesh leaves during evacuation.
      s.fg.position.lerp(tmp.copy(v3(fx, fy, fz)), 0.08);
      const st = val(sh, "state");
      const evac = st === "to_safe" || st === "evacuated" || st === "to_face";
      if (evac) {
        const world = v3(px, py, pz), faceW = v3(fx, fy, fz);
        s.m.position.set(world.x - faceW.x, world.y - faceW.y, world.z - faceW.z);
        s.m.rotation.y = Math.atan2(-(py - fy), px - fx);
      } else {
        placeShovel(s, s.fg.position.x, -s.fg.position.z);
      }
      const blasted = val(twin(face), "blasted_t");
      if (blasted != null) s.muck.count = Math.round(Math.max(3, Math.min(40, blasted / 3000)));
      s.m.userData.body.material.color.set(st === "loading" ? "#ffb020" : st === "down" || st === "stopped" ? "#ff5a5a"
        : evac ? "#ffb020" : "#d0d7de");
      s.m.userData.house.rotation.y = st === "loading" ? (1 - Math.cos(now / 700)) / 2 * s.swing : 0;
      const closed = val(pit, face === "face-lower" ? "zone_lower" : "zone_upper") === "closed";
      const phase = val(pit, "blast_phase");
      s.dome.visible = s.ring.visible = closed;
      s.gas.visible = closed && phase === "gas";
      if (s.gas.visible) s.gas.children.forEach((c, i) => { c.position.y = 50 + 25 * Math.sin(now / 1500 + i); });
      s.zoneLbl.visible = closed;
      if (closed) {
        let ztxt = phase === "evacuation" ? "эвакуация — взрыв"
          : (phase === "gas" && blastMode !== "B" ? "газ · проба ВГСЧ" : "закрыто: газ после взрыва");
        if (phase === "gas") {
          const d = twin("drone-01");
          const co = val(d, face === "face-lower" ? "co_lower" : "co_upper");
          const no2 = val(d, face === "face-lower" ? "no2_lower" : "no2_upper");
          if (co != null || no2 != null) {
            ztxt += ` · CO ${Number(co).toFixed(1)} NO₂ ${Number(no2).toFixed(1)}`;
          }
          if (blastMode === "B" && val(d, "state") === "flying") ztxt = "облёт дрона · " + ztxt;
        }
        setText(s.zoneLbl, ztxt);
      }
    }

    for (const [name, o] of Object.entries(overlays)) {
      const closed = val(twin(name), "state") === "closed";
      o.red.visible = o.lbl.visible = closed;
      o.amber.visible = name === "road-dump-2" && !!val(twin("dump-02"), "in_use");
    }

    const cst = val(twin("crusher-01"), "state");
    cBody.material.color.set(cst === "jammed" || cst === "stopped" || cst === "blocked" ? "#c0392b" : "#c9a227");
    const fill = val(twin("stockpile-01"), "fill") ?? 0.3;
    const sk = 0.35 + 0.85 * Math.sqrt(fill);
    stock.scale.setScalar(sk);
    stock.position.y = sBase + 20 * VZ * sk;
    const pst = val(twin("plant-01"), "state");
    if (plantRoof) plantRoof.material.color.set(pst === "stopped" ? "#ff5a5a" : pst === "starved" ? "#ffb020" : "#3ccf7a");
    const fr = freshRadius("dump-01");
    freshDump.visible = fr > 1;
    const frx = Math.min(fr * 1.2, top.rx), fry = Math.min(fr * 0.8, top.ry);
    const off = top.rx - frx;
    freshDump.scale.set(frx, 5 * VZ, fry);
    freshDump.position.set(top.x + Math.cos(toPit) * off, dumpTop - 1, -(top.y + Math.sin(toPit) * off * top.ry / top.rx));
    if (reserve) reserve.scale.setScalar(1 + freshRadius("dump-02") / 110);

    for (const [name, m] of Object.entries(supportMeshes)) {
      const s = support[name];
      if (!s || s.tx == null) continue;
      const p = truckPos(s, now);
      const prev = m.userData.last;
      if (prev && Math.hypot(p.x - prev.x, p.y - prev.y) > 0.5) m.rotation.y = Math.atan2(p.y - prev.y, p.x - prev.x);
      m.userData.last = { x: p.x, y: p.y };
      m.position.set(p.x, at(p.x, p.y), -p.y);
      m.visible = true;
      m.userData.body.material.color.set(s.state === "down" ? "#ff5a5a" : name.startsWith("dozer") ? "#e0b830" : "#e8c94a");
    }

    droneMesh.visible = drone.flying;
    if (drone.flying) {
      const p = truckPos(drone, now);
      droneMesh.position.set(p.x, at(p.x, p.y) + 70, -p.y);
      droneMesh.userData.rotors.forEach((r, i) => { r.rotation.y = now / 40 + i; });
    }

    rain.visible = !!val(twin("whatif-01"), "rain");
    if (rain.visible) {
      const a = rainGeo.attributes.position;
      for (let i = 0; i < RAIN; i++) {
        let y = a.getY(i) - 14;
        if (y < -250) y += 1150;
        a.setY(i, y);
      }
      a.needsUpdate = true;
      rain.position.set(controls.target.x, controls.target.y - 100, controls.target.z);
    }

    if (now - lastText > 500) {
      lastText = now;
      setText(dumpLbl, `Отвал вскрыши · ${fmt(val(twin("dump-01"), "tonnes"))} т`);
      if (reserve) {
        const inUse = !!val(twin("dump-02"), "in_use");
        setText(reserveLbl, inUse ? `Резервный отвал — в работе · ${fmt(val(twin("dump-02"), "tonnes"))} т` : "Резервный отвал",
          inUse ? "#ffb020" : "");
        reserve.children.forEach((c) => c.material.color.set(inUse ? "#7d6a46" : "#4a443a"));
        const rep = val(twin("workshop-01"), "trucks_in_repair") || 0, wq = val(twin("workshop-01"), "queue") || 0;
        setText(shopLbl, rep ? `Мастерская · в ремонте ${rep}${wq ? `, ждут ${wq}` : ""}` : "Мастерская", rep ? "#e05cc0" : "");
      }
      setText(crusherLbl, cst === "jammed" ? "Дробилка — зависание" : cst === "stopped" ? "Дробилка — остановлена"
        : cst === "blocked" ? "Дробилка — склад полон" : "Дробилка Metso 60-89",
        cst === "jammed" || cst === "stopped" || cst === "blocked" ? "#ff7b7b" : "");
      setText(stockLbl, `Склад руды · ${fmt((val(twin("stockpile-01"), "level_t") || 0) / 1000, 1)} тыс. т`,
        fill > 0.97 ? "#ff7b7b" : "");
      if (fuelLbl) {
        const busy = val(twin("fuel-01"), "fueling") || 0, fq = val(twin("fuel-01"), "queue") || 0;
        setText(fuelLbl, busy ? `Заправка · ${busy}${fq ? ` + очередь ${fq}` : ""}` : "Заправка", busy ? "#2bb3a3" : "");
      }
      if (plantLbl) setText(plantLbl, pst === "stopped" ? "Фабрика — остановлена" : pst === "starved" ? "Фабрика — нет руды" : "Обогатительная фабрика",
        pst === "stopped" ? "#ff7b7b" : pst === "starved" ? "#ffb020" : "");
      for (const [face, s] of Object.entries(shovels)) {
        const sh = twin(s.f.shovel), st = val(sh, "state"), q = val(sh, "queue") || 0;
        const evac = st === "to_safe" || st === "evacuated" || st === "to_face";
        const tag = st === "down" ? " · отказ" : st === "stopped" ? " · остановлен" : st === "no_rock" ? " · нет взорванной массы"
          : evac ? " · эвакуация" : "";
        setText(s.lbl, `${s.f.shovel} · ${FACE_LABEL[face]}${q ? ` · очередь ${q}` : ""}${tag}`, tag ? "#ffb020" : "");
        const phase = val(pit, "blast_phase");
        setText(s.zoneLbl, phase === "evacuation" ? "эвакуация — взрыв"
          : (phase === "gas" && val(twin("control-01"), "blast_mode") !== "B" ? "газ · проба ВГСЧ" : "закрыто: газ после взрыва"));
      }
      badge.style.display = follow ? "block" : "none";
      if (follow) badge.textContent = `камера следует за ${follow} · Esc или двойной щелчок — отпустить`;
    }

    if (follow) {
      const m = follow === "drone-01" ? droneMesh : truckMeshes[follow];
      if (m) {
        tmp.copy(m.position).sub(controls.target).multiplyScalar(0.12);
        controls.target.add(tmp);
        camera.position.add(tmp);
      }
    }
  }

  let raf = 0;
  const placeSun = () => {
    const dist = camera.position.distanceTo(controls.target);
    const half = Math.max(350, Math.min(1600, dist * 0.9));
    const c = sun.shadow.camera;
    if (c.right !== half) { Object.assign(c, { left: -half, right: half, top: half, bottom: -half }); c.updateProjectionMatrix(); }
    sun.target.position.copy(controls.target);
    sun.position.copy(controls.target).addScaledVector(SUN_DIR, 3000);
  };
  function loop(now) {
    raf = requestAnimationFrame(loop);
    update(now);
    controls.update();
    placeSun();
    renderer.render(scene, camera);
    labels.render(scene, camera);
  }
  function resize() {
    const w = container.clientWidth, h = container.clientHeight;
    renderer.setSize(w, h);
    labels.setSize(w, h);
    camera.aspect = w / Math.max(1, h);
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  const onKey = (e) => { if (e.key === "Escape") follow = null; };
  window.addEventListener("keydown", onKey);

  return {
    active: false,
    start() { this.active = true; container.style.display = "block"; resize(); cancelAnimationFrame(raf); raf = requestAnimationFrame(loop); },
    stop() { this.active = false; container.style.display = "none"; cancelAnimationFrame(raf); },
    bench(n = 20) {
      const t0 = performance.now();
      for (let i = 0; i < n; i++) {
        update(t0 + i * 16);
        renderer.render(scene, camera);
        labels.render(scene, camera);
      }
      renderer.getContext().finish();
      return (performance.now() - t0) / n;
    },
    screenshot() { update(performance.now()); placeSun(); renderer.render(scene, camera); return renderer.domElement.toDataURL("image/jpeg", 0.85); },
    dispose() {
      this.stop();
      window.removeEventListener("resize", resize);
      window.removeEventListener("keydown", onKey);
      scene.traverse((o) => {
        o.geometry?.dispose();
        for (const mat of [].concat(o.material || [])) mat.dispose();
      });
      renderer.dispose();
      renderer.domElement.remove();
      labels.domElement.remove();
      badge.remove();
    },
    look(target, pos) { controls.target.copy(v3(...target)); camera.position.copy(v3(...pos)); controls.update(); placeSun(); },
  };
}
