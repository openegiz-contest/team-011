"use strict";

const NS = "neta.pit";
const API = "/ditto/api/2";
const SHIFT_S = 12 * 3600;
const COLORS = {
  to_face: "#4ea1ff", hauling: "#3ccf7a", loading: "#ffb020", queued_at_shovel: "#ff5a5a",
  queued_at_dump: "#ff5a5a", dumping: "#b77dff", awaiting_dispatch: "#8892a6", parked: "#8892a6",
  to_workshop: "#e05cc0", repair: "#e05cc0", handover: "#5d6573", break: "#5d6573", refuel: "#2bb3a3",
  pre_start: "#5d6573", to_parking: "#9aa4b5", to_fuel: "#2bb3a3", repair_queue: "#e05cc0", breakdown: "#ff2d2d",
};
const WORKING = new Set(["to_face", "hauling", "loading", "queued_at_shovel", "queued_at_dump", "dumping", "awaiting_dispatch"]);
const LOAD_COLORS = { ore: "#c47a35", waste: "#9a9488", empty: "#2b3038" };
const WHATIF_DEFAULTS = {
  shovel_01_down: false, shovel_02_down: false, crusher_stop: false, road_dump_closed: false,
  rain: false, poor_blast: false, calm_wind: false, trucks_in_repair: 0, plant_stop: false, pit_stage: 0,
  dozer_down: false,
};
const WHATIF_LABEL = {
  shovel_01_down: "экскаватор вскрыши остановлен", shovel_02_down: "экскаватор руды остановлен",
  crusher_stop: "дробилка остановлена", road_dump_closed: "дорога на отвал закрыта", rain: "дождь",
  poor_blast: "плохо взорванный блок", calm_wind: "безветрие при взрыве", plant_stop: "фабрика остановлена",
  dozer_down: "бульдозер на отвале стоит",
};
const STAGE_YEARS = [0, 2, 4, 6];
const FACE_LABEL = { "face-upper": "вскрыша", "face-lower": "руда" };
const STATE_RU = {
  to_face: "порожний рейс к забою", hauling: "гружёный рейс", loading: "погрузка",
  queued_at_shovel: "очередь у экскаватора", queued_at_dump: "очередь на разгрузку", dumping: "разгрузка",
  awaiting_dispatch: "ждёт назначения", parked: "стоянка", to_workshop: "едет в мастерскую", repair: "ремонт",
  idle: "ожидает", blast: "закрыт на взрыв", down: "отказ", stopped: "остановлен", crushing: "дробит",
  jammed: "зависание", open: "открыта", closed: "закрыта", flying: "облёт", landed: "на базе",
  "face-upper": "верхний забой (вскрыша)", "face-lower": "нижний забой (руда)", crusher: "дробилка",
  dump: "отвал", "dump-2": "резервный отвал", workshop: "мастерская",
  handover: "пересменка", break: "обед на стоянке", refuel: "заправка", pre_start: "предсменный осмотр, ждёт выезда",
  to_parking: "едет на стоянку", to_fuel: "едет на заправку", repair_queue: "ждёт места в мастерской",
  breakdown: "сломался в пути, ремонт на месте", no_rock: "нет взорванной массы", working: "работает",
  grading: "профилирует дороги", parking: "стоянка", fuel: "заправка",
  blocked: "склад полон — ждёт", running: "работает", starved: "нет руды", docked: "на базе",
};
const FEAT_RU = {
  state: "состояние", face: "забой", location: "где", payload: "в кузове", fuel_l: "дизель за смену",
  trips: "рейсов", eta_s: "прибытие (время смены)", queue: "очередь", utilization: "загрузка",
  load_end_s: "конец погрузки", blocked_until_s: "закрыт до", pocket_t: "бункер", boulders: "негабаритов",
  jams: "зависаний", rockbreaks: "разбито гидромолотом", down_min: "простой", x50_m: "x50 руды",
  tonnes: "принято", in_use: "в работе", trucks_in_repair: "в ремонте", sweeps: "облётов",
  co_lower: "CO, руда", no2_lower: "NO₂, руда", co_upper: "CO, вскрыша", no2_upper: "NO₂, вскрыша",
  rolling_resistance: "сопротивление качению", trucks: "самосвалов на дороге",
  level_t: "на складе", fill: "заполнение", rate_tph: "темп", starved_min: "без руды", stopped_min: "остановлена",
  blocked_min: "ждёт места на складе", mined_t: "вынуто", blasted_t: "в развале", advance_m: "забой продвинулся",
  volume_m3: "объём", shovel_state: "экскаватор", zone: "зона", trucks_assigned: "самосвалов назначено",
  tank_pct: "бак", fueling: "заправляются", refuels: "заправок", dispensed_l: "выдано дизеля",
  cu_t: "медь в концентрате", concentrate_t: "концентрат", cu_ore_t: "медь в добытой руде",
  cu_value_usd: "стоимость меди", field_repairs: "ремонт на дороге", queue_min: "ожидание места",
  blocked_no_rock: "нет взорванной массы", evacuated: "выводов из забоев", breakdowns: "поломок в пути",
  headway_min: "потери на обгоне",
};
const HIDDEN = new Set(["x", "y", "z", "load_end_s", "blocked_until_s"]);

const things = {};
const trucks = {};
let geometry = null;
let view = null;
let base = null;
let terrain = null;
let hits = [];
let hover = null;
const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");

const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? "—" : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: d, minimumFractionDigits: d }));
const val = (thing, feature) => thing?.features?.[feature]?.properties?.value;
const $ = (id) => document.getElementById(id);
const twin = (name) => things[`${NS}:${name}`];

async function getJson(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function putValue(feature, value, thing = "control-01") {
  await fetch(`${API}/things/${NS}:${thing}/features/${feature}/properties/value`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
  });
  const r = await fetch(`${API}/things/${NS}:${thing}`);
  if (r.ok) merge(await r.json());
}
const putWhatif = (feature, value) => putValue(feature, value, "whatif-01");

function onWhatif(t) {
  let active = 0;
  const labels = [];
  document.querySelectorAll(".scen").forEach((el) => {
    const on = !!val(t, el.dataset.feature);
    el.classList.toggle("on", on);
    el.querySelector("button").setAttribute("aria-checked", String(on));
    if (on) { active += 1; labels.push(WHATIF_LABEL[el.dataset.feature]); }
  });
  const repair = val(t, "trucks_in_repair") || 0;
  $("repair").value = String(repair);
  if (repair) { active += 1; labels.push(`в ремонте: ${repair} самосв.`); }
  const stage = Number(val(t, "pit_stage") || 0);
  $("pit_stage").value = String(stage);
  if (stage) { active += 1; labels.push(`карьер через ${STAGE_YEARS[stage]} ${stage === 3 ? "лет" : "года"}`); }
  $("scen_count").textContent = `Активных сценариев: ${active}`;
  $("banner").innerHTML = labels.map((l) => `<span>${l}</span>`).join("");
}

function merge(update) {
  const id = update.thingId;
  if (!id) return;
  const t = (things[id] ||= { thingId: id, features: {} });
  if (update.attributes) t.attributes = { ...(t.attributes || {}), ...update.attributes };
  if (update.features) for (const [k, f] of Object.entries(update.features)) t.features[k] = f;
  const name = id.split(":")[1];
  if (name.startsWith("truck-")) moveTruck(name, t);
  if (name === "pit-01") onPit(t);
  if (name === "control-01") onControl(t);
  if (name === "dispatcher-01") onDispatcher(t);
  if (name === "crusher-01" || name === "crusher-guard-01") onCrusher();
  if (name === "pit-01" || name === "drone-01") onBlast();
  if (name === "drone-01") moveDrone(t);
  if (name === "whatif-01") onWhatif(t);
  if (name === "plant-01" || name === "stockpile-01" || name.startsWith("face-")) onPlant();
  if (name.startsWith("face-") && geometry) buildPaths();
  if (name === "advisor-01") onAdvice(t);
  if (["fuel-01", "workshop-01", "dozer-01", "grader-01", "pit-01"].includes(name)) onService();
  if (name === "dozer-01" || name === "grader-01") moveSupport(name, t);
  if (name === "pit-01" && geometry && t.attributes?.geometry && t.attributes.geometry !== geometry) onGeometry(t.attributes.geometry);
}

function onGeometry(g) {
  geometry = g;
  buildPaths();
  for (const tr of Object.values(trucks)) tr.track = null;
  fit();
  if (view3d) {
    const active = view3d.active;
    view3d.dispose();
    view3d = null;
    if (active) setView("3d");
  }
}

const facePos = (face) => {
  const t = twin(face), x = val(t, "x");
  return x == null ? geometry.faces[face].position : [x, val(t, "y"), val(t, "z")];
};

const PLANT_STATE = { running: "работает", starved: "простой: нет руды на складе", stopped: "остановлена (сценарий)" };
function onPlant() {
  const pl = twin("plant-01"), sp = twin("stockpile-01"), c = twin("crusher-01");
  if (!pl || !sp) return;
  const st = val(pl, "state");
  $("plant_state").textContent = `${PLANT_STATE[st] || st || "—"}${st === "running" ? ` · ${fmt(val(pl, "rate_tph"))} т/ч` : ""}`;
  $("plant_state").style.color = st === "stopped" ? "#ff7b7b" : st === "starved" ? "#ffb020" : "";
  const fill = val(sp, "fill") || 0;
  $("stockpile").textContent = `${fmt(val(sp, "level_t"))} т · ${fmt(fill * 100)} %`;
  $("stockpile_fill").style.width = `${Math.min(100, fill * 100)}%`;
  $("stockpile_fill").style.background = fill > 0.97 ? "#ff5a5a" : fill < 0.05 ? "#ffb020" : "";
  $("plant_t").textContent = `${fmt(val(pl, "tonnes"))} т`;
  const idle = (val(pl, "starved_min") || 0) + (val(pl, "stopped_min") || 0);
  $("plant_idle").textContent = idle ? `${fmt(idle)} мин` : "нет";
  const bl = val(c, "blocked_min") || 0;
  $("crusher_blocked").textContent = bl ? `${fmt(bl)} мин` : "нет";
  $("crusher_blocked").style.color = bl ? "#ff7b7b" : "";
  const fv = (f, k) => val(twin(f), k);
  $("advance").textContent = `${fmt(fv("face-lower", "advance_m"))} / ${fmt(fv("face-upper", "advance_m"))} м`;
  $("blasted").textContent = `${fmt((fv("face-lower", "blasted_t") || 0) / 1000)} / ${fmt((fv("face-upper", "blasted_t") || 0) / 1000)} тыс. т`;
  const pit = twin("pit-01");
  $("cu_ore").textContent = `${fmt(val(pit, "cu_ore_t"), 1)} т · $${fmt((val(pit, "cu_value_usd") || 0) / 1000)} тыс.`;
  $("cu_plant").textContent = `${fmt(val(pl, "cu_t"), 1)} т меди · ${fmt(val(pl, "concentrate_t"))} т конц.`;
}

const DOZER_STATE = { working: "работает", down: "СТОИТ — отвал принимает по одному" };
const GRADER_STATE = { parked: "на базе (сухо)", grading: "профилирует дороги" };
function onService() {
  const f = twin("fuel-01"), w = twin("workshop-01"), dz = twin("dozer-01"), gr = twin("grader-01"), p = twin("pit-01");
  if (!f || !w) return;
  const fq = val(f, "queue") || 0;
  $("fuel_state").textContent = `${fmt(val(f, "fueling"))} на заправке${fq ? `, очередь ${fq}` : ""} · всего ${fmt(val(f, "refuels"))}`;
  $("fuel_l").textContent = `${fmt(val(f, "dispensed_l"))} л`;
  const wq = val(w, "queue") || 0, fr = val(w, "field_repairs") || 0;
  $("ws_state").textContent = `в ремонте ${fmt(val(w, "trucks_in_repair"))}${wq ? `, ждут места ${wq}` : ""}${fr ? `, на дороге ${fr}` : ""}`;
  $("ws_state").style.color = fr || wq ? "#ffb020" : "";
  $("breakdowns").textContent = fmt(val(p, "breakdowns"));
  const ds = val(dz, "state");
  $("dozer_state").textContent = DOZER_STATE[ds] || ds || "—";
  $("dozer_state").style.color = ds === "down" ? "#ff7b7b" : "";
  $("grader_state").textContent = `${GRADER_STATE[val(gr, "state")] || "—"} · сопротивление ${fmt((val(gr, "rolling_resistance") || 0.02) * 100, 1)} %`;
  $("evacuated").textContent = fmt(val(p, "evacuated"));
  $("headway").textContent = `${fmt(val(p, "headway_min"))} мин`;
}

const support = {};
function moveSupport(name, t) {
  const now = performance.now();
  const s = (support[name] ||= { fx: val(t, "x"), fy: val(t, "y"), tx: val(t, "x"), ty: val(t, "y"), t0: now });
  const p = truckPos(s, now);
  Object.assign(s, { fx: p.x, fy: p.y, tx: val(t, "x"), ty: val(t, "y"), t0: now, state: val(t, "state") });
}

let advice = [];
function onAdvice(t) {
  try { advice = JSON.parse(val(t, "items") || "[]"); } catch { return; }
  const esc = (x) => String(x).replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
  $("advice").innerHTML = advice.length ? advice.map((r, i) => `
    <div class="adv ${r.level}">
      <div class="adv-who">${esc(r.who)}</div>
      <div>${esc(r.text)}</div>
      ${r.effect ? `<div class="adv-eff">Эффект: ${esc(r.effect)}</div>` : ""}
      ${r.action ? `<button data-i="${i}">${esc(r.action.label)}</button>` : ""}
    </div>`).join("") : '<p class="hint">Замечаний нет.</p>';
  $("adv_n").textContent = advice.length || "";
  $("adv_n").className = `badge ${advice.some((r) => r.level === "critical") ? "critical" : advice.length ? "warn" : ""}`;
  let hist = [];
  try { hist = JSON.parse(val(t, "history") || "[]"); } catch { /* keep empty */ }
  const done = hist.filter((h) => h.until);
  $("advice_hist_n").textContent = done.length ? `(${done.length})` : "";
  $("advice_hist").innerHTML = done.length ? done.map((h) => `
    <div class="hist ${h.level}"><span class="when">${esc(h.since)}–${esc(h.until)}</span>
      <span>${esc(h.who)}: ${esc(h.text.split(". ")[0])}.</span>
      <span class="res">${h.applied === true ? "выполнено" : h.applied === false ? "не выполнено, снято" : "снято"}</span></div>`).join("")
    : '<p class="hint">Пока нет закрытых советов.</p>';
}
$("advice").addEventListener("click", (e) => {
  const a = advice[e.target.dataset?.i]?.action;
  if (a) putValue(a.feature, a.value, a.thing);
});

const drone = { x: 0, y: 0, fx: 0, fy: 0, tx: 0, ty: 0, t0: 0, flying: false };
function moveDrone(t) {
  const now = performance.now();
  const p = truckPos(drone, now);
  const flying = val(t, "state") === "flying";
  if (flying && !drone.flying) Object.assign(drone, { fx: val(t, "x"), fy: val(t, "y") });
  else Object.assign(drone, { fx: p.x, fy: p.y });
  Object.assign(drone, { tx: val(t, "x"), ty: val(t, "y"), t0: now, flying });
}

const CRUSHER_STATE = { crushing: "дробит", idle: "ждёт руду", jammed: "ЗАВИСАНИЕ — расчистка", stopped: "остановлена", blocked: "склад полон — ждёт" };
function onCrusher() {
  const c = twin("crusher-01"), g = twin("crusher-guard-01");
  const st = val(c, "state");
  $("crusher_state").textContent = CRUSHER_STATE[st] || st || "—";
  $("crusher_state").style.color = st === "jammed" || st === "blocked" ? "#ff7b7b" : "";
  $("boulders").textContent = fmt(val(c, "boulders"));
  $("jams").textContent = fmt(val(c, "jams"));
  $("rockbreaks").textContent = fmt(val(c, "rockbreaks"));
  $("crusher_down").textContent = `${fmt(val(c, "down_min"))} мин`;
  $("x50").textContent = `${fmt(val(c, "x50_m"), 2)} м`;
  if (val(g, "threshold_m")) $("threshold").textContent = fmt(val(g, "threshold_m"), 2);
}

const PHASE = { planned: "взрыв в 06:00 смены", evacuation: "эвакуация из карьера", gas: "газ после взрыва", clear: "карьер открыт" };
const H_PHASE = { planned: "в 06:00", evacuation: "эвакуация", gas: "газ, закрыто", clear: "открыто" };
const ZONE = { open: "открыта", closed: "закрыта" };
function onBlast() {
  const p = twin("pit-01"), d = twin("drone-01");
  const ph = val(p, "blast_phase");
  $("blast_phase").textContent = PHASE[ph] || ph || "—";
  $("blast_phase").style.color = ph === "gas" || ph === "evacuation" ? "#ffb020" : "";
  $("h_blast").textContent = H_PHASE[ph] || "—";
  $("h_blast").className = ph === "gas" || ph === "evacuation" ? "warn" : ph === "clear" ? "ok" : "";
  $("wind").textContent = val(p, "wind_ms") ? `${fmt(val(p, "wind_ms"), 1)} м/с` : "—";
  $("zones").textContent = `${ZONE[val(p, "zone_lower")] || "—"} / ${ZONE[val(p, "zone_upper")] || "—"}`;
  const gas = (id, f, pdk) => {
    const v = val(d, f);
    $(id).textContent = v ? `${fmt(v, 1)} мг/м³` : "—";
    $(id).style.color = v >= pdk ? "#ff7b7b" : v ? "#3ccf7a" : "";
  };
  gas("co_lower", "co_lower", 20);
  gas("no2_lower", "no2_lower", 2);
  $("sweeps").textContent = fmt(val(d, "sweeps"));
  $("shut").textContent = val(p, "ore_face_shut_min") ? `${fmt(val(p, "ore_face_shut_min"))} мин` : "—";
  const ex = val(p, "gas_exposure_min");
  $("exposure").textContent = ex ? `${fmt(ex, 1)} мин` : "нет";
  $("exposure").style.color = ex ? "#ff7b7b" : "";
}

let paths = null;
// As a face advances along its cut, the bench road is extended behind it:
// from the current face point back along the cut to where the route starts.
function faceSpur(face) {
  const f = geometry.faces[face], cut = f?.cut, start = Object.entries(geometry.routes)
    .find(([n]) => n.startsWith(`${face}->`))?.[1][0];
  if (!cut?.length || !start) return null;
  const p = facePos(face);
  const near = (q) => cut.reduce((b, c, i) => (Math.hypot(c[0] - q[0], c[1] - q[1]) < Math.hypot(cut[b][0] - q[0], cut[b][1] - q[1]) ? i : b), 0);
  const kp = near(p), k0 = near(start);
  if (Math.hypot(p[0] - start[0], p[1] - start[1]) < 10) return null;
  const pts = [p];
  const step = kp > k0 ? -1 : 1;
  for (let k = kp + step; k !== k0; k += step) pts.push(cut[k]);
  pts.push(start);
  return pts;
}

function buildPaths() {
  const routes = Object.entries(geometry.routes).map(([name, pts]) => {
    const spur = faceSpur(name.split("->")[0]);
    return spur ? [...spur.slice(0, -1), ...pts] : pts;
  });
  paths = [...routes, ...Object.values(geometry.roads || {})].map((pts) => {
    const cum = [0];
    for (let i = 1; i < pts.length; i++) {
      const [a, b] = [pts[i - 1], pts[i]];
      cum.push(cum[i - 1] + Math.hypot(b[0] - a[0], b[1] - a[1], (b[2] || 0) - (a[2] || 0)));
    }
    return { pts, cum };
  });
}

function project(path, x, y, z) {
  let best = { d: Infinity, s: 0 };
  const { pts, cum } = path;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    const dx = b[0] - a[0], dy = b[1] - a[1], dz = (b[2] || 0) - (a[2] || 0);
    const len2 = dx * dx + dy * dy + dz * dz;
    const u = len2 ? Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy + (z - (a[2] || 0)) * dz) / len2)) : 0;
    const d = Math.hypot(a[0] + u * dx - x, a[1] + u * dy - y, (a[2] || 0) + u * dz - z);
    if (d < best.d) best = { d, s: cum[i - 1] + u * (cum[i] - cum[i - 1]) };
  }
  return best;
}

function pointAt(path, s) {
  const { pts, cum } = path;
  let i = 1;
  while (i < cum.length - 1 && cum[i] < s) i++;
  const a = pts[i - 1], b = pts[i];
  const u = cum[i] > cum[i - 1] ? (s - cum[i - 1]) / (cum[i] - cum[i - 1]) : 0;
  return { x: a[0] + u * (b[0] - a[0]), y: a[1] + u * (b[1] - a[1]), z: (a[2] || 0) + u * ((b[2] || 0) - (a[2] || 0)),
    dir: Math.atan2(-(b[1] - a[1]), b[0] - a[0]) };
}

function findPath(from, to) {
  if (!paths) return null;
  let best = null;
  for (const path of paths) {
    const p0 = project(path, from.x, from.y, from.z), p1 = project(path, to.x, to.y, to.z);
    if (p0.d > 25 || p1.d > 25) continue;
    const cost = p0.d + p1.d + 0.01 * Math.abs(p1.s - p0.s);
    if (!best || cost < best.cost) best = { cost, path, s0: p0.s, s1: p1.s };
  }
  return best;
}

const QUEUE_GAP = 45;
function queueSlot(name, tr) {
  if ((tr.state !== "queued_at_shovel" && tr.state !== "queued_at_dump") || tr.track || !paths) return null;
  const same = Object.entries(trucks).filter(([, o]) => o.state === tr.state && o.location === tr.location)
    .map(([n]) => n).sort();
  const rank = same.indexOf(name) + 1;
  const atStart = tr.state === "queued_at_shovel";
  const path = paths.find((p) => {
    const q = atStart ? p.pts[0] : p.pts[p.pts.length - 1];
    return Math.hypot(q[0] - tr.tx, q[1] - tr.ty) < 5;
  });
  if (!path) return null;
  const total = path.cum[path.cum.length - 1];
  const s = atStart ? Math.min(total, rank * QUEUE_GAP) : Math.max(0, total - rank * QUEUE_GAP);
  const p = pointAt(path, s);
  return { ...p, heading: atStart ? p.dir + Math.PI : p.dir, ends: Math.min(s, total - s) };
}

// Right-hand traffic: on a road a truck keeps to its lane, easing into the
// centre line near loading and dumping points.
const LANE = 10;
function keepRight(p, ends) {
  const k = LANE * Math.max(0, Math.min(1, ends / 40));
  return { ...p, x: p.x - Math.sin(p.heading) * k, y: p.y - Math.cos(p.heading) * k };
}

function truckDisplay(name, tr, now) {
  const q = queueSlot(name, tr);
  if (q) return keepRight(q, q.ends);
  const p = { ...truckPos(tr, now), heading: tr.heading || 0 };
  if (tr.location === "parking" && ["handover", "pre_start", "break", "parked"].includes(tr.state) && !tr.track && geometry?.bays?.length) {
    const b = geometry.bays, n = b.length;
    const vx = geometry.parking[0] - b.reduce((s, q) => s + q[0], 0) / n;
    const vy = geometry.parking[1] - b.reduce((s, q) => s + q[1], 0) / n;
    p.heading = Math.atan2(-vy, vx);
  }
  return p.ends == null ? p : keepRight(p, p.ends);
}

function moveTruck(name, t) {
  const x = val(t, "x"), y = val(t, "y"), z = val(t, "z") || 0;
  if (x == null) return;
  const now = performance.now();
  const tr = (trucks[name] ||= { fx: x, fy: y, fz: z, tx: x, ty: y, tz: z, t0: now, heading: 0 });
  const p = truckPos(tr, now);
  const moved = Math.hypot(x - p.x, y - p.y) > 2;
  tr.track = moved ? findPath(p, { x, y, z }) : null;
  if (moved && !tr.track) tr.heading = Math.atan2(-(y - p.y), x - p.x);
  Object.assign(tr, { fx: p.x, fy: p.y, fz: p.z, tx: x, ty: y, tz: z, t0: now, state: val(t, "state"),
    payload: val(t, "payload"), face: val(t, "face"), location: val(t, "location") });
}

function truckPos(tr, now) {
  const k = Math.min(1, (now - tr.t0) / 1000);
  if (tr.track) {
    const { path, s0, s1 } = tr.track;
    const s = s0 + (s1 - s0) * k, p = pointAt(path, s);
    tr.heading = s1 >= s0 ? p.dir : p.dir + Math.PI;
    return { ...p, ends: Math.min(s, path.cum[path.cum.length - 1] - s) };
  }
  return { x: tr.fx + (tr.tx - tr.fx) * k, y: tr.fy + (tr.ty - tr.fy) * k, z: (tr.fz || 0) + ((tr.tz || 0) - (tr.fz || 0)) * k };
}

// --- panels ---------------------------------------------------------------
const trend = { shift: null, last: -1, pts: [] };
function onPit(t) {
  const s = val(t, "sim_time_s") || 0;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  $("clock").textContent = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")} / 12:00`;
  $("progress").style.width = `${Math.min(100, (s / SHIFT_S) * 100)}%`;
  $("shift").textContent = val(t, "shift") == null ? "—" : `№${val(t, "shift") + 1}`;
  $("total_t").textContent = `${fmt(val(t, "total_t"))} т`;
  $("h_total").textContent = `${fmt(val(t, "total_t"))} т`;
  $("h_rate").textContent = `${fmt(val(t, "rate_tph"))} т/ч`;
  $("h_cu").textContent = `${fmt(val(t, "cu_ore_t"), 1)} т`;
  const fleet = Object.values(trucks);
  const busy = fleet.filter((tr) => WORKING.has(tr.state)).length;
  $("h_fleet").textContent = fleet.length ? `${busy} из ${fleet.length}` : "—";
  $("split").textContent = `${fmt(val(t, "ore_t"))} / ${fmt(val(t, "waste_t"))} т`;
  $("rate").textContent = `${fmt(val(t, "rate_tph"))} т/ч`;
  $("queue").textContent = `${fmt(val(t, "queue_h"), 1)} ч`;
  $("lpt_ore").textContent = `${fmt(val(t, "l_per_t_ore"), 3)} л/т`;
  $("lpt_waste").textContent = `${fmt(val(t, "l_per_t_waste"), 3)} л/т`;
  $("fallbacks").textContent = fmt(val(t, "dispatch_fallbacks"));
  recordShift(val(t, "last_shift"));
  const shift = val(t, "shift");
  if (shift !== trend.shift || s < trend.last) Object.assign(trend, { shift, last: -1, pts: [[0, 0, 0]] });
  if (s - trend.last >= 60 || !trend.pts.length) {
    trend.pts.push([s, val(t, "ore_t") || 0, val(t, "waste_t") || 0]);
    trend.last = s;
    drawTrend();
  }
}

function drawTrend() {
  const c = $("trend");
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const top = Math.max(1000, ...trend.pts.map((p) => p[1] + p[2])) * 1.1;
  const px = (s) => (s / SHIFT_S) * w, py = (v) => h - (v / top) * (h - 12);
  g.strokeStyle = "#1b222c"; g.lineWidth = 1;
  for (let hr = 2; hr < 12; hr += 2) { g.beginPath(); g.moveTo(px(hr * 3600), 0); g.lineTo(px(hr * 3600), h); g.stroke(); }
  const area = (lo, hi, color) => {
    if (trend.pts.length < 2) return;
    g.beginPath();
    trend.pts.forEach((p, i) => (i ? g.lineTo(px(p[0]), py(hi(p))) : g.moveTo(px(p[0]), py(hi(p)))));
    for (let i = trend.pts.length - 1; i >= 0; i--) g.lineTo(px(trend.pts[i][0]), py(lo(trend.pts[i])));
    g.closePath(); g.fillStyle = color; g.fill();
  };
  area(() => 0, (p) => p[1], "rgba(196,122,53,.85)");
  area((p) => p[1], (p) => p[1] + p[2], "rgba(154,148,136,.55)");
  g.fillStyle = "#8b949e"; g.font = "11px system-ui";
  g.fillText(`${fmt(top / 1.1)} т`, 4, 11);
}

function onControl(t) {
  document.querySelectorAll(".toggle").forEach((el) => {
    const v = val(t, el.dataset.feature);
    el.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.v === v));
  });
  const sp = val(t, "speedup");
  if (sp != null) $("speedup").value = String(sp);
}

function onDispatcher(t) {
  $("decisions").textContent = fmt(val(t, "decisions"));
  const truck = val(t, "last_truck"), face = val(t, "last_face");
  $("last_decision").textContent = truck ? `${truck} → ${FACE_LABEL[face] || face}` : "—";
  $("targets").textContent = `${fmt(val(t, "target_lower_tph"))} / ${fmt(val(t, "target_upper_tph"))} т/ч`;
}

const HISTORY_KEY = "neta-pit-history";
function recordShift(s) {
  if (typeof s === "string") { try { s = JSON.parse(s); } catch (_) { s = null; } }
  if (!s || s.shift == null) return renderHistory();
  const hist = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  const key = `${s.shift}|${s.seed}|${s.dispatch_mode}|${s.oversize_mode}|${s.blast_mode}|${s.total_t}`;
  if (!hist.some((h) => h.key === key)) {
    hist.unshift({ key, ...s });
    localStorage.setItem(HISTORY_KEY, JSON.stringify(hist.slice(0, 30)));
  }
  renderHistory();
}

function renderHistory() {
  const hist = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  if (!hist.length) return;
  $("history").innerHTML = hist.map((h) => `<tr><td>№${h.shift + 1} (seed ${h.seed})</td><td>${h.dispatch_mode}</td>` +
    `<td>${h.oversize_mode ?? "—"}</td><td>${h.blast_mode ?? "—"}</td><td>${fmt(h.total_t)}</td><td>${fmt(h.ore_t)}</td>` +
    `<td>${fmt(h.waste_t)}</td><td>${fmt(h.queue_h, 1)}</td><td>${fmt(h.crusher_down_min)}</td>` +
    `<td>${fmt(h.ore_face_shut_min)}</td><td>${fmt(h.gas_exposure_min, 1)}</td>` +
    `<td>${fmt(h.l_per_t_ore, 3)}</td><td>${fmt(h.l_per_t_waste, 3)}</td><td>${fmt(h.plant_t)}</td>` +
    `<td>${h.pit_stage ? `+${STAGE_YEARS[h.pit_stage]} г.` : "сейчас"}</td></tr>`).join("");
}

// --- map: camera --------------------------------------------------------------
function fit() {
  const dpr = window.devicePixelRatio || 1;
  const r = canvas.getBoundingClientRect();
  canvas.width = r.width * dpr;
  canvas.height = r.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!geometry) return;
  const pts = [...geometry.ramp, geometry.crusher, geometry.waste_dump,
    ...(geometry.reserve_dump ? [geometry.reserve_dump, geometry.workshop] : []),
    ...(geometry.plant ? [geometry.plant] : []),
    [-geometry.pit.a_m, -geometry.pit.b_m], [geometry.pit.a_m, geometry.pit.b_m]];
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const minX = Math.min(...xs) - 200, maxX = Math.max(...xs) + 300;
  const minY = Math.min(...ys) - 120, maxY = Math.max(...ys) + 160;
  const bottom = 90;
  const s = Math.min((r.width - 40) / (maxX - minX), (r.height - bottom - 30) / (maxY - minY));
  base = { s, ox: (r.width - (maxX - minX) * s) / 2 - minX * s, oy: (r.height - bottom - (maxY - minY) * s) / 2 + maxY * s + 10 };
  view = { ...base };
  if (!terrain) terrain = buildTerrain();
}

function zoomAt(mx, my, k) {
  const s = Math.max(base.s * 0.7, Math.min(base.s * 10, view.s * k));
  const wx = (mx - view.ox) / view.s, wy = (view.oy - my) / view.s;
  view = { s, ox: mx - wx * s, oy: my + wy * s };
}

const X = (x) => view.ox + x * view.s;
const Y = (y) => view.oy - y * view.s;
const sym = () => Math.min(2.2, Math.max(1, Math.sqrt(view.s / base.s)));

function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildTerrain() {
  const rnd = mulberry32(7);
  const contours = [];
  for (let k = 0; k < 10; k++) {
    const r0 = 1000 + k * 170;
    const ph = [rnd() * 6.28, rnd() * 6.28, rnd() * 6.28];
    const pts = [];
    for (let i = 0; i <= 140; i++) {
      const a = (i / 140) * Math.PI * 2;
      const r = r0 * (1 + 0.07 * Math.sin(3 * a + ph[0]) + 0.04 * Math.sin(5 * a + ph[1]) + 0.02 * Math.sin(11 * a + ph[2]));
      pts.push([300 + r * Math.cos(a) * 1.3, 350 + r * Math.sin(a) * 0.95]);
    }
    contours.push(pts);
  }
  const patches = Array.from({ length: 320 }, () => [-2200 + rnd() * 5000, -1500 + rnd() * 3800, 12 + rnd() * 45, rnd()]);
  return { contours, patches };
}

// --- map: drawing ---------------------------------------------------------------
function polyline(points, color, width) {
  ctx.beginPath();
  points.forEach((p, i) => (i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.stroke();
}

// Labels are queued while the scene is drawn and placed last: warnings first,
// then names, then minor captions; a label that would overlap one already
// placed is nudged up or down, and minor captions are dropped if still blocked.
let labels = [];
const ALERT = new Set(["#ff7b7b", "#ff9b9b", "#ffb020", "#e05cc0", "#2bb3a3"]);
function label(text, x, y, color = "#e6edf3", size = 12) {
  const prio = ALERT.has(color) ? 0 : size >= 12 ? 1 : 2;
  labels.push({ text, sx: X(x), sy: Y(y), color, size, prio });
}

function flushLabels() {
  const placed = [];
  const free = (b) => !placed.some((o) => b.x < o.x + o.w && o.x < b.x + b.w && b.y < o.y + o.h && o.y < b.y + b.h);
  labels.sort((a, b) => a.prio - b.prio);
  for (const l of labels) {
    ctx.font = `600 ${l.size}px system-ui`;
    const w = ctx.measureText(l.text).width + 12, h = l.size + 8;
    let box = null;
    for (const dy of [0, -h - 2, h + 2, -2 * h - 4, 2 * h + 4]) {
      const b = { x: l.sx - w / 2, y: l.sy - h / 2 - l.size * 0.35 + dy, w, h };
      if (free(b)) { box = b; break; }
    }
    if (!box) {
      if (l.prio === 2) continue;
      box = { x: l.sx - w / 2, y: l.sy - h / 2 - l.size * 0.35, w, h };
    }
    placed.push(box);
    ctx.beginPath();
    ctx.roundRect(box.x, box.y, box.w, box.h, h / 2);
    ctx.fillStyle = l.prio === 0 ? "rgba(13,17,23,.88)" : "rgba(13,17,23,.68)";
    ctx.fill();
    if (l.prio === 0) { ctx.strokeStyle = l.color; ctx.globalAlpha = 0.55; ctx.lineWidth = 1; ctx.stroke(); ctx.globalAlpha = 1; }
    ctx.fillStyle = l.color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(l.text, box.x + w / 2, box.y + h / 2 + 0.5);
  }
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  labels = [];
}

function ellipse(x, y, rx, ry, fill, stroke) {
  ctx.beginPath();
  ctx.ellipse(X(x), Y(y), Math.max(1, rx * view.s), Math.max(1, ry * view.s), 0, 0, Math.PI * 2);
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); }
}

function drawGround(w, h) {
  const g = ctx.createRadialGradient(X(300), Y(400), 30, X(300), Y(400), 2800 * view.s);
  g.addColorStop(0, "#22231d"); g.addColorStop(1, "#13160f");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  const { a_m, b_m } = geometry.pit;
  for (const [x, y, r, t] of terrain.patches) {
    if ((x / (a_m + 120)) ** 2 + (y / (b_m + 120)) ** 2 < 1) continue;
    ellipse(x, y, r, r * 0.7, t < 0.6 ? "rgba(96,110,62,.13)" : "rgba(130,112,84,.12)");
  }
  for (const c of terrain.contours) polyline(c, "rgba(190,175,130,.07)", 1);
  ctx.strokeStyle = "rgba(255,255,255,.03)";
  ctx.lineWidth = 1;
  const step = 250;
  const x0 = Math.floor(-view.ox / view.s / step) * step, x1 = (w - view.ox) / view.s;
  for (let x = x0; x <= x1; x += step) { ctx.beginPath(); ctx.moveTo(X(x), 0); ctx.lineTo(X(x), h); ctx.stroke(); }
  const y0 = Math.floor((view.oy - h) / view.s / step) * step, y1 = view.oy / view.s;
  for (let y = y0; y <= y1; y += step) { ctx.beginPath(); ctx.moveTo(0, Y(y)); ctx.lineTo(w, Y(y)); ctx.stroke(); }
}

function drawPit() {
  const { a_m, b_m, wall_slope, benches_z } = geometry.pit;
  const floor = Math.min(...benches_z);
  ellipse(0, 0, a_m + 30, b_m + 30, "#2e2a23");
  for (let z = 0; z >= floor; z -= 15) {
    const shrink = -z * wall_slope;
    const a = (a_m - shrink) * view.s, b = (b_m - shrink) * view.s;
    const d = z / floor;
    const g = ctx.createLinearGradient(X(0) - a, Y(0) - b, X(0) + a, Y(0) + b);
    const c = (k) => `rgb(${Math.round((104 - d * 58) * k)},${Math.round((90 - d * 50) * k)},${Math.round((72 - d * 40) * k)})`;
    g.addColorStop(0, c(0.72)); g.addColorStop(0.5, c(1)); g.addColorStop(1, c(1.18));
    ctx.beginPath();
    ctx.ellipse(X(0), Y(0), a, b, 0, 0, Math.PI * 2);
    ctx.fillStyle = g;
    ctx.fill();
    const work = benches_z.includes(z);
    ctx.strokeStyle = work ? "rgba(240,205,150,.6)" : "rgba(225,195,150,.2)";
    ctx.lineWidth = work ? 1.6 : 1;
    ctx.stroke();
  }
  const rnd = mulberry32(11);
  for (const [name, f] of Object.entries(geometry.faces)) {
    const [ox, oy] = f.position;
    const [fx, fy] = facePos(name);
    const ore = f.material === "ore";
    if (ore) {
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(X(ox + 40), Y(oy - 30), 260 * view.s, 150 * view.s, -0.35, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(196,122,53,.13)"; ctx.fill();
      ctx.setLineDash([5, 5]); ctx.strokeStyle = "rgba(220,140,60,.55)"; ctx.lineWidth = 1.2; ctx.stroke();
      ctx.restore();
      label("рудное тело", ox + 40, oy - 210, "#d08a45", 11);
    }
    const out = Math.atan2(fy, fx);
    const rocks = Math.round(Math.max(3, Math.min(40, (val(twin(name), "blasted_t") ?? 78000) / 3000)));
    for (let i = 0; i < rocks; i++) {
      const a = out + (rnd() - 0.5) * 1.6, r = 25 + rnd() * 60;
      ellipse(fx + Math.cos(a) * r, fy + Math.sin(a) * r, 5 + rnd() * 9, 4 + rnd() * 6,
        ore ? `rgba(${170 + rnd() * 40},${100 + rnd() * 30},50,.75)` : `rgba(${130 + rnd() * 30},${125 + rnd() * 25},${115 + rnd() * 20},.7)`);
    }
  }
  benches_z.forEach((z, i) => {
    const shrink = -z * wall_slope, th = Math.PI * (1.08 + 0.1 * i);
    label(`уступ ${z} м`, (a_m - shrink) * Math.cos(th) + 70, (b_m - shrink) * Math.sin(th), "#c9b48f", 11);
  });
  label(`карьер ${Math.round(2 * a_m)}×${Math.round(2 * b_m)} м, глубина ${-floor} м`, 0, -b_m - 70, "#8b949e", 11);
}

function road(pts, width = 28) {
  const lw = Math.max(3, width * view.s);
  polyline(pts, "#191714", lw + 3);
  polyline(pts, "#6f685c", lw);
  if (lw > 8) { ctx.setLineDash([6, 9]); polyline(pts, "rgba(245,235,205,.28)", 1); ctx.setLineDash([]); }
}

function drawRoads() {
  for (const [name, pts] of Object.entries(geometry.routes)) {
    if (!name.endsWith("dump-2")) road(pts);
  }
  for (const face of Object.keys(geometry.faces)) {
    const spur = faceSpur(face);
    if (spur) road(spur, 24);
  }
  road(geometry.ramp);
  const inUse = val(twin("dump-02"), "in_use");
  for (const [name, pts] of Object.entries(geometry.roads || {})) {
    if (name === "road-ramp") continue;
    const closed = val(twin(name), "state") === "closed";
    if (closed) {
      ctx.setLineDash([10, 7]);
      polyline(pts, "#ff5a5a", Math.max(3, 18 * view.s));
      ctx.setLineDash([]);
      const [x, y] = pts[Math.floor(pts.length / 2)];
      label("✕ дорога закрыта", x, y + 50, "#ff7b7b");
    } else if (name === "road-dump-2") {
      if (inUse) { road(pts); polyline(pts, "rgba(255,176,32,.55)", Math.max(2, 10 * view.s)); }
      else { ctx.setLineDash([4, 7]); polyline(pts, "#4a4f57", 2); ctx.setLineDash([]); }
    } else if (name === "road-service") {
      road(pts, 20);
    }
  }
}

function pile(x, y, rx, ry, colors) {
  colors.forEach((c, i) => {
    const k = 1 - i * 0.24;
    ellipse(x - i * 6, y + i * 6, rx * k, ry * k, c, "rgba(0,0,0,.28)");
  });
}

function building(x, y, wm, hm, fill, edge, minW = 18, minH = 13) {
  const w = Math.max(minW, wm * view.s), h = Math.max(minH, hm * view.s);
  ctx.fillStyle = "rgba(0,0,0,.35)";
  ctx.fillRect(X(x) - w / 2 + 3, Y(y) - h / 2 + 3, w, h);
  ctx.fillStyle = fill;
  ctx.fillRect(X(x) - w / 2, Y(y) - h / 2, w, h);
  ctx.strokeStyle = edge;
  ctx.lineWidth = 1.5;
  ctx.strokeRect(X(x) - w / 2, Y(y) - h / 2, w, h);
  return { w, h };
}

function hit(id, sx, sy, r) { hits.push({ id, sx, sy, r }); }

function drawSite() {
  drawRoads();

  const [dx, dy] = geometry.waste_dump;
  pile(dx, dy, 170, 110, ["#4a4238", "#585044", "#675e50", "#776d5d"]);
  const fresh = freshRadius("dump-01");
  if (fresh) {
    const a = Math.atan2(-dy, -dx);
    ellipse(dx + Math.cos(a) * 120, dy + Math.sin(a) * 75, fresh * 1.2, fresh * 0.8, "#9c9280", "rgba(0,0,0,.35)");
  }
  label(`Отвал вскрыши · ${fmt(val(twin("dump-01"), "tonnes"))} т`, dx, dy + 135);
  hit("dump-01", X(dx), Y(dy), 30);

  if (geometry.reserve_dump) {
    const [rx, ry] = geometry.reserve_dump;
    const inUse = val(twin("dump-02"), "in_use");
    const grow = 1 + freshRadius("dump-02") / 110;
    pile(rx, ry, 110 * grow, 75 * grow, inUse ? ["#5a4a30", "#6c5a3a", "#7d6a46"] : ["#2e2a24", "#36312a"]);
    const t2 = val(twin("dump-02"), "tonnes");
    label(inUse ? `Резервный отвал — в работе · ${fmt(t2)} т` : "Резервный отвал", rx, ry + 100, inUse ? "#ffb020" : "#8b949e");
    hit("dump-02", X(rx), Y(ry), 24);

    if (geometry.bays?.length) {
      const b = geometry.bays, [ax, ay] = b[0], [bx, by] = b[b.length - 1];
      const L = Math.hypot(bx - ax, by - ay) || 1, ux = (bx - ax) / L, uy = (by - ay) / L;
      const corner = (s, n) => [X(ax + ux * s - uy * n), Y(ay + uy * s + ux * n)];
      ctx.fillStyle = "#3a3833";
      ctx.beginPath();
      [[-16, -22], [L + 16, -22], [L + 16, 22], [-16, 22]].forEach(([s, n], i) => ctx[i ? "lineTo" : "moveTo"](...corner(s, n)));
      ctx.fill();
      ctx.strokeStyle = "rgba(230,220,190,.35)"; ctx.lineWidth = 1;
      for (let i = 0; i <= b.length; i++) {
        const s = (i - 0.5) * (L / Math.max(1, b.length - 1));
        ctx.beginPath(); ctx.moveTo(...corner(s, -18)); ctx.lineTo(...corner(s, 18)); ctx.stroke();
      }
      const [px, py] = [(ax + bx) / 2 - uy * 40, (ay + by) / 2 + ux * 40];
      label("Стоянка", px, py, "#8b949e", 11);
    }

    const [wx, wy] = geometry.workshop;
    building(wx, wy, 70, 45, "#2f2a36", "#e05cc0", 26, 17);
    const ws = twin("workshop-01");
    const rep = val(ws, "trucks_in_repair") || 0, wq = val(ws, "queue") || 0;
    label(rep ? `Мастерская · в ремонте ${rep}${wq ? `, ждут ${wq}` : ""}` : "Мастерская (2 места)", wx, wy - 70, rep ? "#e05cc0" : "#8b949e");
    hit("workshop-01", X(wx), Y(wy), 18);

    if (geometry.fuel) {
      const [fx, fy] = geometry.fuel_station || geometry.fuel;
      const fu = twin("fuel-01"), busy = val(fu, "fueling") || 0, fq = val(fu, "queue") || 0;
      building(fx, fy, 30, 22, "#1f3a38", busy ? "#2bb3a3" : "#3d6b66", 14, 10);
      label(`Заправка${busy ? ` · ${busy}${fq ? `+${fq}` : ""}` : ""}`, fx, fy + 45, busy ? "#2bb3a3" : "#8b949e", 11);
      hit("fuel-01", X(fx), Y(fy), 14);
    }
  }

  const [cx, cy] = geometry.crusher;
  const crusher = twin("crusher-01");
  const cst = val(crusher, "state");
  ctx.fillStyle = "#34322d";
  const pad = [Math.max(30, 160 * view.s), Math.max(22, 110 * view.s)];
  ctx.fillRect(X(cx) - pad[0] / 2, Y(cy) - pad[1] / 2, pad[0], pad[1]);
  const [sx, sy] = geometry.stockpile || [cx + 260, cy + 80];
  const sp = twin("stockpile-01"), plant = twin("plant-01");
  const spFill = val(sp, "fill") ?? 0.3;
  if (geometry.plant) {
    const [px, py] = geometry.plant;
    polyline([[sx, sy], [px - 70, py]], "#9aa0a6", Math.max(2, 6 * view.s));
    const pst = val(plant, "state");
    const [pf, pe] = pst === "stopped" ? ["#7a2626", "#ff5a5a"] : pst === "starved" ? ["#6b5420", "#ffb020"] : ["#2f5d46", "#3ccf7a"];
    building(px, py, 140, 90, pf, pe, 34, 22);
    building(px + 20, py, 40, 50, "rgba(255,255,255,.08)", pe, 10, 12);
    label(pst === "stopped" ? "Фабрика — остановлена" : pst === "starved" ? "Фабрика — нет руды" : "Обогатительная фабрика",
      px, py + 80, pst === "stopped" ? "#ff7b7b" : pst === "starved" ? "#ffb020" : "#e6edf3");
    hit("plant-01", X(px), Y(py), 26);
  }
  polyline([[cx, cy], [sx - 30, sy - 8]], "#9aa0a6", Math.max(2, 6 * view.s));
  const k = 0.35 + 0.85 * Math.sqrt(spFill);
  pile(sx, sy, 75 * k, 50 * k, ["#7a4a22", "#9a5d2a", "#b8733a"]);
  label(`Склад руды · ${fmt((val(sp, "level_t") || 0) / 1000, 1)} тыс. т`, sx, sy - 70, spFill > 0.97 ? "#ff7b7b" : "#d08a45", 11);
  hit("stockpile-01", X(sx), Y(sy), 22);
  const bad = cst === "jammed" || cst === "stopped" || cst === "blocked";
  const { w } = building(cx, cy, 45, 45, bad ? "#7a2626" : "#8a6d1c", bad ? "#ff5a5a" : "#e0b830", 20, 20);
  const pocket = val(crusher, "pocket_t") || 0;
  const bh = 26, fill = Math.min(1, pocket / 450);
  ctx.fillStyle = "#222a35"; ctx.fillRect(X(cx) + w / 2 + 5, Y(cy) - bh / 2, 6, bh);
  ctx.fillStyle = "#e0b830"; ctx.fillRect(X(cx) + w / 2 + 5, Y(cy) + bh / 2 - bh * fill, 6, bh * fill);
  label("Дробилка Metso 60-89", cx, cy + 95);
  if (cst === "jammed") label("зависание — расчистка", cx, cy - 85, "#ff7b7b");
  if (cst === "stopped") label("остановлена", cx, cy - 85, "#ff7b7b");
  if (cst === "blocked") label("склад полон — дробилка ждёт", cx, cy - 85, "#ff7b7b");
  hit("crusher-01", X(cx), Y(cy), 20);

  const pit = twin("pit-01");
  const now = performance.now();
  for (const [face, f] of Object.entries(geometry.faces)) {
    const [fx, fy] = facePos(face);
    const shovel = twin(f.shovel);
    const st = val(shovel, "state");
    if (val(pit, face === "face-lower" ? "zone_lower" : "zone_upper") === "closed") {
      ctx.beginPath();
      ctx.arc(X(fx), Y(fy), 170 * view.s, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,90,90,.16)"; ctx.fill();
      ctx.strokeStyle = "rgba(255,90,90,.75)"; ctx.setLineDash([6, 4]); ctx.lineWidth = 1.5; ctx.stroke();
      ctx.setLineDash([]);
      label(val(pit, "blast_phase") === "evacuation" ? "эвакуация" : "закрыто: газ", fx, fy + 140, "#ff9b9b");
    }
    drawShovel(fx, fy, st, now);
    const q = val(shovel, "queue") || 0;
    label(`${f.shovel} · ${FACE_LABEL[face]}${q ? ` · очередь ${q}` : ""}`, fx, fy - 75);
    if (st === "down") label("отказ", fx, fy + 95, "#ff7b7b");
    if (st === "stopped") label("остановлен (сценарий)", fx, fy + 95, "#ff7b7b");
    if (st === "no_rock") label("нет взорванной массы — ждёт взрыва", fx, fy + 95, "#ffb020");
    if (st === "handover") label("пересменка", fx, fy + 95, "#8b949e");
    hit(f.shovel, X(fx), Y(fy), 16);
  }
}

function freshRadius(dump) {
  const v = val(twin(dump), "volume_m3") || 0;
  return Math.sqrt(v / 5 / Math.PI);
}

function drawShovel(fx, fy, st, now) {
  const k = sym();
  const facing = -Math.atan2(fy, fx);
  const swing = st === "loading" ? Math.sin(now / 450) * 0.9 : 0;
  const color = st === "loading" ? "#ffb020" : st === "down" || st === "stopped" ? "#ff5a5a"
    : st === "blast" ? "#ff7b00" : "#d0d7de";
  ctx.save();
  ctx.translate(X(fx), Y(fy));
  ctx.scale(k, k);
  ctx.rotate(facing);
  ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.fillRect(-10, -9, 22, 20);
  ctx.fillStyle = "#1e2228"; ctx.fillRect(-12, -10, 22, 6); ctx.fillRect(-12, 4, 22, 6);
  ctx.rotate(swing);
  ctx.fillStyle = color; ctx.fillRect(-9, -7, 15, 14);
  ctx.strokeStyle = "#0d1117"; ctx.lineWidth = 1; ctx.strokeRect(-9, -7, 15, 14);
  ctx.strokeStyle = color; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(5, 0); ctx.lineTo(22, 0); ctx.stroke();
  ctx.fillStyle = "#3a3f47"; ctx.fillRect(21, -5, 6, 10);
  ctx.restore();
}

function drawTrucks(now) {
  const k = sym();
  for (const [name, tr] of Object.entries(trucks)) {
    const p = truckDisplay(name, tr, now);
    const material = geometry.faces[tr.face]?.material;
    const loaded = (tr.payload || 0) > 1;
    ctx.save();
    ctx.translate(X(p.x), Y(p.y));
    ctx.rotate(p.heading);
    ctx.scale(k, k);
    ctx.fillStyle = "rgba(0,0,0,.4)"; ctx.fillRect(-7, -3, 16, 9);
    ctx.fillStyle = loaded ? LOAD_COLORS[material] || LOAD_COLORS.waste : LOAD_COLORS.empty;
    ctx.fillRect(-9, -5, 11, 10);
    ctx.fillStyle = COLORS[tr.state] || "#8892a6";
    ctx.fillRect(2, -5, 6, 10);
    ctx.strokeStyle = COLORS[tr.state] || "#8892a6"; ctx.lineWidth = 1.2; ctx.strokeRect(-9, -5, 17, 10);
    ctx.restore();
    if (hover === name) {
      ctx.beginPath(); ctx.arc(X(p.x), Y(p.y), 14 * k, 0, Math.PI * 2);
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
    }
    ctx.font = "10px system-ui";
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(13,17,23,.8)";
    const n = name.replace("truck-", "");
    ctx.strokeText(n, X(p.x) + 10 * k, Y(p.y) - 6 * k);
    ctx.fillStyle = "#c9d1d9"; ctx.fillText(n, X(p.x) + 10 * k, Y(p.y) - 6 * k);
    hit(name, X(p.x), Y(p.y), 10 * k);
  }
}

function drawSupport(now) {
  const k = sym();
  for (const [name, s] of Object.entries(support)) {
    if (s.tx == null) continue;
    const p = truckPos(s, now);
    const dozer = name.startsWith("dozer"), down = s.state === "down";
    const color = down ? "#ff5a5a" : dozer ? "#e0b830" : s.state === "grading" ? "#ffd84d" : "#a89a5a";
    ctx.save();
    ctx.translate(X(p.x), Y(p.y));
    ctx.scale(k, k);
    ctx.fillStyle = "rgba(0,0,0,.4)"; ctx.fillRect(-7, -4, 16, 10);
    ctx.fillStyle = color;
    if (dozer) { ctx.fillRect(-6, -5, 12, 10); ctx.fillStyle = "#3a3f47"; ctx.fillRect(7, -7, 3, 14); }
    else { ctx.fillRect(-10, -3, 20, 6); ctx.fillStyle = "#3a3f47"; ctx.fillRect(-2, -6, 3, 12); }
    ctx.restore();
    const text = dozer ? (down ? "бульдозер — стоит" : "бульдозер") : (s.state === "grading" ? "грейдер" : "");
    if (text) label(text, p.x, p.y + 40, down ? "#ff7b7b" : "#c9b45a", 10);
    hit(name, X(p.x), Y(p.y), 10 * k);
  }
}

function drawDrone(now) {
  if (!drone.flying) return;
  const p = truckPos(drone, now);
  ctx.save();
  ctx.translate(X(p.x), Y(p.y));
  ctx.strokeStyle = "#00e5ff"; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(-9, -9); ctx.lineTo(9, 9); ctx.moveTo(9, -9); ctx.lineTo(-9, 9); ctx.stroke();
  const spin = now / 60;
  for (const [ax, ay] of [[-9, -9], [9, 9], [9, -9], [-9, 9]]) {
    ctx.beginPath(); ctx.ellipse(ax, ay, 5, 1.6, spin, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.restore();
  label("дрон", p.x, p.y + 50, "#00e5ff");
  hit("drone-01", X(p.x), Y(p.y), 14);
}

function drawRain(w, h) {
  if (!val(twin("whatif-01"), "rain")) return;
  ctx.fillStyle = "rgba(60,120,220,.07)";
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(140,180,255,.28)";
  ctx.lineWidth = 1;
  const off = (performance.now() / 12) % 40;
  for (let x = -40; x < w + 40; x += 40) {
    for (let y = -40; y < h; y += 40) {
      ctx.beginPath(); ctx.moveTo(x + off / 3, y + off); ctx.lineTo(x + off / 3 - 4, y + off + 10); ctx.stroke();
    }
  }
}

function drawScale(w, h) {
  const nice = [50, 100, 200, 250, 500, 1000, 2000];
  const m = nice.find((n) => n * view.s >= 70) || 2000;
  const len = m * view.s;
  const x = w - len - 24, y = h - 22;
  ctx.fillStyle = "rgba(13,17,23,.7)"; ctx.fillRect(x - 8, y - 20, len + 16, 28);
  ctx.strokeStyle = "#c9d1d9"; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(x, y - 4); ctx.lineTo(x, y); ctx.lineTo(x + len, y); ctx.lineTo(x + len, y - 4); ctx.stroke();
  ctx.fillStyle = "#c9d1d9"; ctx.font = "11px system-ui"; ctx.textAlign = "center";
  ctx.fillText(`${m} м`, x + len / 2, y - 7);
  const nx = w - 26, ny = 72;
  ctx.beginPath(); ctx.moveTo(nx, ny - 16); ctx.lineTo(nx + 7, ny + 4); ctx.lineTo(nx, ny); ctx.lineTo(nx - 7, ny + 4); ctx.closePath();
  ctx.fillStyle = "#c9d1d9"; ctx.fill();
  ctx.fillText("С", nx, ny + 17);
  ctx.textAlign = "left";
}

let view3d = null;
const HINT_2D = "колесо — масштаб · перетаскивание — сдвиг · наведите на технику, чтобы увидеть её двойник";
const HINT_3D = "левая кнопка — вращение · правая — сдвиг · колесо — масштаб · щелчок по самосвалу — камера за ним";
async function setView(mode) {
  const three = mode === "3d";
  if (three && !geometry) return;
  if (three && !view3d) {
    try {
      const m = await import("./three-view.js");
      view3d = m.createView($("map3d"));
    } catch (e) {
      console.error(e);
      $("v3d").disabled = true;
      $("v3d").title = "3D недоступно в этом браузере (нужен WebGL)";
      return;
    }
  }
  $("v2d").classList.toggle("active", !three);
  $("v3d").classList.toggle("active", three);
  canvas.style.visibility = three ? "hidden" : "";
  $("mapctl").style.display = three ? "none" : "";
  $("maphint").textContent = three ? HINT_3D : HINT_2D;
  hover = null;
  $("tip").style.display = "none";
  if (three) view3d.start(); else view3d?.stop();
}

function frame(now) {
  if (view3d?.active) { requestAnimationFrame(frame); return; }
  const r = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, r.width, r.height);
  if (geometry && view) {
    hits = [];
    drawGround(r.width, r.height);
    drawPit();
    drawSite();
    drawSupport(now);
    drawTrucks(now);
    drawDrone(now);
    flushLabels();
    drawRain(r.width, r.height);
    drawScale(r.width, r.height);
    if (hover && Math.floor(now / 500) !== frame.tick) { frame.tick = Math.floor(now / 500); showTip(); }
  }
  requestAnimationFrame(frame);
}

// --- map: interaction ------------------------------------------------------------
const tip = { x: 0, y: 0 };
const UNIT_RU = { L: "л", t: "т", m: "м", min: "мин", s: "с", h: "ч", "m/s": "м/с", "t/h": "т/ч", "mg/m3": "мг/м³", m3: "м³" };
function fmtFeature(k, p) {
  const v = p.value;
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (typeof v === "string") return STATE_RU[v] || v || "—";
  if (k === "utilization" || k === "fill") return `${fmt(v * 100)} %`;
  if (k === "eta_s") return `${String(Math.floor(v / 3600)).padStart(2, "0")}:${String(Math.floor((v % 3600) / 60)).padStart(2, "0")}`;
  if (typeof v === "number") {
    const unit = UNIT_RU[p.unit] ?? p.unit;
    return `${fmt(v, Math.abs(v) < 10 && v % 1 ? 2 : 0)}${unit ? ` ${unit}` : ""}`;
  }
  return "—";
}

function showTip() {
  const el = $("tip");
  const t = twin(hover);
  if (!t) { el.style.display = "none"; return; }
  const a = t.attributes || {};
  const rows = Object.entries(t.features || {})
    .filter(([k, f]) => !HIDDEN.has(k) && f.properties && typeof f.properties.value !== "object")
    .slice(0, 10)
    .map(([k, f]) => `<tr><td>${FEAT_RU[k] || k}</td><td>${fmtFeature(k, f.properties)}</td></tr>`).join("");
  el.innerHTML = `<b>${t.thingId}</b>${a.model ? `<i>${a.model}${a.capacity_t ? ` · ${fmt(a.capacity_t, 1)} т` : ""}</i>` : ""}` +
    `<table>${rows}</table><small>цифровой двойник в Ditto · поток SSE</small>`;
  el.style.display = "block";
  const box = canvas.getBoundingClientRect();
  el.style.left = `${Math.min(tip.x + 16, box.width - el.offsetWidth - 8)}px`;
  el.style.top = `${Math.min(tip.y + 16, box.height - el.offsetHeight - 8)}px`;
}

function initMapInput() {
  let drag = null;
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const b = canvas.getBoundingClientRect();
    zoomAt(e.clientX - b.left, e.clientY - b.top, e.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, { passive: false });
  canvas.addEventListener("mousedown", (e) => { drag = { x: e.clientX, y: e.clientY }; canvas.classList.add("drag"); });
  window.addEventListener("mouseup", () => { drag = null; canvas.classList.remove("drag"); });
  canvas.addEventListener("mousemove", (e) => {
    const b = canvas.getBoundingClientRect();
    tip.x = e.clientX - b.left; tip.y = e.clientY - b.top;
    if (drag) {
      view.ox += e.clientX - drag.x; view.oy += e.clientY - drag.y;
      drag = { x: e.clientX, y: e.clientY };
      return;
    }
    let best = null, bd = Infinity;
    for (const h of hits) {
      const d = Math.hypot(h.sx - tip.x, h.sy - tip.y);
      const score = d - (h.id.startsWith("truck-") || h.id === "drone-01" ? 12 : 0);
      if (d < h.r + 6 && score < bd) { best = h.id; bd = score; }
    }
    hover = best;
    canvas.style.cursor = best ? "pointer" : "";
    if (best) showTip(); else $("tip").style.display = "none";
  });
  canvas.addEventListener("mouseleave", () => { hover = null; $("tip").style.display = "none"; });
  canvas.addEventListener("dblclick", () => { view = { ...base }; });
  const center = () => { const b = canvas.getBoundingClientRect(); return [b.width / 2, b.height / 2]; };
  $("zoom_in").addEventListener("click", () => zoomAt(...center(), 1.3));
  $("zoom_out").addEventListener("click", () => zoomAt(...center(), 1 / 1.3));
  $("zoom_reset").addEventListener("click", () => { view = { ...base }; });
}

// --- live stream ------------------------------------------------------------
function connect() {
  const es = new EventSource(`${API}/things?namespaces=${NS}`);
  es.onopen = () => { $("link").textContent = "поток Ditto SSE"; $("link").className = "link on"; };
  es.onerror = () => { $("link").textContent = "нет связи с Ditto"; $("link").className = "link off"; };
  es.onmessage = (e) => { if (e.data) { try { merge(JSON.parse(e.data)); } catch (_) { /* heartbeat */ } } };
}

async function init() {
  document.querySelectorAll(".toggle button").forEach((b) =>
    b.addEventListener("click", () => putValue(b.closest(".toggle").dataset.feature, b.dataset.v)));
  $("restart").addEventListener("click", () => putValue("run", (val(twin("control-01"), "run") || 0) + 1));
  $("speedup").addEventListener("change", (e) => putValue("speedup", Number(e.target.value)));
  document.querySelectorAll(".scen button").forEach((b) => b.setAttribute("aria-label", b.previousElementSibling.textContent));
  document.querySelectorAll(".scen button").forEach((b) => b.addEventListener("click", () => {
    const f = b.closest(".scen").dataset.feature;
    putWhatif(f, !val(twin("whatif-01"), f));
  }));
  $("repair").addEventListener("change", (e) => putWhatif("trucks_in_repair", Number(e.target.value)));
  $("pit_stage").addEventListener("change", (e) => putWhatif("pit_stage", Number(e.target.value)));
  $("blast_now").addEventListener("click", () => putWhatif("blast_now", (val(twin("whatif-01"), "blast_now") || 0) + 1));
  $("scen_reset").addEventListener("click", () => {
    for (const [f, v] of Object.entries(WHATIF_DEFAULTS)) {
      if (val(twin("whatif-01"), f) !== v) putWhatif(f, v);
    }
  });
  window.addEventListener("resize", () => { fit(); drawTrend(); });
  initMapInput();
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((o) => {
      o.classList.toggle("active", o === b);
      o.setAttribute("aria-selected", String(o === b));
    });
    document.querySelectorAll(".pane").forEach((p) => { p.hidden = p.dataset.pane !== b.dataset.tab; });
    if (b.dataset.tab === "kpi") drawTrend();
  }));
  $("v2d").addEventListener("click", () => setView("2d"));
  $("v3d").addEventListener("click", () => setView("3d"));
  renderHistory();
  for (;;) {
    try {
      const res = await getJson(`/search/things?namespaces=${NS}&option=size(100)`);
      res.items.forEach(merge);
      geometry = twin("pit-01")?.attributes?.geometry;
      if (geometry) { buildPaths(); onPlant(); break; }
    } catch (e) { console.warn(e); }
    await new Promise((r) => setTimeout(r, 2000));
  }
  fit();
  connect();
  requestAnimationFrame(frame);
  const q = new URLSearchParams(location.search);
  if (q.get("tab")) document.querySelector(`.tabs [data-tab="${q.get("tab")}"]`)?.click();
  if (q.get("view") === "3d") setView("3d");
}

init();
