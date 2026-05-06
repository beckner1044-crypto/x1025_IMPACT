'use strict';

// Global application state
const APP = {
  theme: document.documentElement.dataset.theme || 'dark',
  apiEndpoint: '',
  fleet: [],
  ports: [],
  vesselIndex: 0,
  telemetry: null,
  map: null,
  mapLayers: { vessel: null, waypoint: null, ports: null, route: null }
};

// Cache for DOM elements
const UI = {};

document.addEventListener('DOMContentLoaded', async () => {
  cacheUI();
  applyTheme(APP.theme);
  bindEvents();
  await loadData();
  seedChat();
});

function cacheUI() {
  [
    'connLabel','themeBtn','themeIcon','healthRing','healthScore','vName','vMmsi','vFlag','vTime',
    'speedVal','speedTrend','speedSpark','headingVal','headingSpark','rpmVal','rpmTrend','rpmSpark',
    'battVal','battSpark','navGrid','engGrid','alertsList','freeStack','checklist','chatWindow',
    'chatForm','chatInput','chatMode','apiEndpoint','saveEndpoint','telemetryPre','apiPre',
    'eventTimeline','prevVessel','nextVessel','fleetIndex','fleetType'
  ].forEach(id => UI[id] = document.getElementById(id));
}

function bindEvents() {
  UI.themeBtn.addEventListener('click', () =>
    applyTheme(APP.theme === 'dark' ? 'light' : 'dark')
  );

  UI.saveEndpoint.addEventListener('click', () => {
    APP.apiEndpoint = UI.apiEndpoint.value.trim();
    UI.connLabel.textContent = APP.apiEndpoint ? 'External API connected' : 'Signal K simulated';
    UI.chatMode.textContent = APP.apiEndpoint
      ? 'External mode: responses from your Python API.'
      : 'Local mode active — no external endpoint configured.';
  });

  UI.chatForm.addEventListener('submit', async e => {
    e.preventDefault();
    const q = UI.chatInput.value.trim();
    if (!q) return;
    UI.chatInput.value = '';
    autoResize(UI.chatInput);
    await handleQuestion(q);
  });

  UI.chatInput.addEventListener('input', () => autoResize(UI.chatInput));

  document.querySelectorAll('[data-q]').forEach(btn =>
    btn.addEventListener('click', () => handleQuestion(btn.dataset.q))
  );

  document.querySelectorAll('.tab').forEach(btn =>
    btn.addEventListener('click', () => activateTab(btn))
  );

  UI.prevVessel.addEventListener('click', () => switchVessel(-1));
  UI.nextVessel.addEventListener('click', () => switchVessel(1));
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

// THEME
function applyTheme(t) {
  APP.theme = t;
  document.documentElement.setAttribute('data-theme', t);
  UI.themeIcon.textContent = t === 'dark' ? '☀' : '☾';
  UI.themeBtn.setAttribute('aria-label', t === 'dark'
    ? 'Switch to light theme'
    : 'Switch to dark theme');
}

// TABS
function activateTab(btn) {
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.remove('active');
    t.setAttribute('aria-selected', 'false');
  });
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

  btn.classList.add('active');
  btn.setAttribute('aria-selected', 'true');
  const panel = document.getElementById('panel-' + btn.dataset.tab);
  if (panel) panel.classList.add('active');
}

// DATA LOADING
async function loadData() {
  const [fleetRes, portsRes] = await Promise.all([
    fetch('fleet-telemetry.json'),
    fetch('ports.json')
  ]);

  APP.fleet = (await fleetRes.json()).fleet;
  APP.ports = (await portsRes.json()).ports;
  APP.vesselIndex = 0;
  APP.telemetry = APP.fleet[0];

  renderAll(APP.telemetry);
  initMap();
  renderMap(APP.telemetry);
}

function switchVessel(delta) {
  APP.vesselIndex = (APP.vesselIndex + delta + APP.fleet.length) % APP.fleet.length;
  APP.telemetry = APP.fleet[APP.vesselIndex];
  renderAll(APP.telemetry);
  renderMap(APP.telemetry);
}

// RENDERING
function renderAll(d) {
  // header
  UI.vName.textContent = d.vessel.name;
  UI.vMmsi.textContent = d.vessel.mmsi;
  UI.vFlag.textContent = d.vessel.flag;
  UI.vTime.textContent = fmtDate(d.timestamp);

  UI.fleetIndex.textContent = `${APP.vesselIndex + 1} / ${APP.fleet.length}`;
  UI.fleetType.textContent = d.vessel.type;

  // health ring
  const score = d.analytics.maintenanceScore;
  UI.healthScore.textContent = score;
  setRing(score);

  // KPIs
  const h = d.history;
  UI.speedVal.textContent = fmt1(last(h.speedKn)) + ' kn';
  UI.headingVal.textContent = fmt0(last(h.headingDeg)) + '°';
  UI.rpmVal.textContent = fmt0(last(h.rpm));
  UI.battVal.textContent = `${fmt1(last(h.batteryVoltage))} V · ${d.energy.batteries.house.socPercent}%`;

  setTrend(UI.speedTrend, h.speedKn);
  setTrend(UI.rpmTrend, h.rpm);

  sparkline(UI.speedSpark, h.speedKn);
  sparkline(UI.headingSpark, h.headingDeg);
  sparkline(UI.rpmSpark, h.rpm);
  sparkline(UI.battSpark, h.batteryVoltage);

  // Navigation & Environment
  renderMetrics(UI.navGrid, [
    ['Latitude', fmt4(d.navigation.position.latitude) + '°'],
    ['Longitude', fmt4(d.navigation.position.longitude) + '°'],
    ['COG', fmt0(d.navigation.courseOverGroundDeg) + '°'],
    ['Depth', fmt1(d.navigation.depthBelowTransducerM) + ' m'],
    ['Apparent wind', fmt1(d.environment.wind.apparentSpeedKn) + ' kn'],
    ['True wind', fmt1(d.environment.wind.trueSpeedKn) + ' kn'],
    ['Water temp', fmt1(d.environment.water.temperatureC) + ' °C'],
    ['Pressure', fmt1(d.environment.weather.pressureHpa) + ' hPa']
  ]);

  // Propulsion & Power
  renderMetrics(UI.engGrid, [
    ['RPM', fmt0(d.propulsion.engine1.rpm)],
    ['Engine load', d.propulsion.engine1.loadPercent + '%'],
    ['Coolant temp', fmt1(d.propulsion.engine1.coolantTempC) + ' °C'],
    ['Oil pressure', fmt0(d.propulsion.engine1.oilPressureKpa) + ' kPa'],
    ['Alternator', fmt1(d.energy.charging.alternatorVoltage) + ' V'],
    ['Fuel level', d.tanks.fuel.main.levelPercent + '%'],
    ['Range estimate', d.analytics.rangeEstimateNm + ' nm'],
    ['AI anomaly', Math.round(d.analytics.anomalyScore * 100) + ' / 100']
  ]);

  // Alerts
  UI.alertsList.innerHTML = d.analytics.alerts.map(a => `
    <article class="alert-card ${a.severity}">
      <div class="row">
        <strong>${a.title}</strong>
        <span class="mini-badge ${a.severity === 'danger' ? 'err' : 'warn'}">${a.severity}</span>
      </div>
      <p>${a.description}</p>
      <p><strong>Action:</strong> ${a.recommendation}</p>
    </article>
  `).join('');

  // Free stack
  UI.freeStack.innerHTML = d.integrations.freeStack.map(s => `
    <div class="stack-item">
      <div>
        <strong>${s.name}</strong>
        <span>${s.role}</span>
      </div>
      <span class="mini-badge acc">free</span>
    </div>
  `).join('');

  // Checklist
  UI.checklist.innerHTML = d.analytics.checklist.map(c => `<li>${c}</li>`).join('');

  // Technical data panes
  UI.telemetryPre.textContent = JSON.stringify(d, null, 2);

  const nearby = findNearbyPorts(d.navigation.position).map(p => ({
    name: p.name,
    fuel: p.fuel,
    distanceNm: distanceNm(
      d.navigation.position.latitude,
      d.navigation.position.longitude,
      p.latitude,
      p.longitude
    ).toFixed(1)
  }));

  UI.apiPre.textContent = JSON.stringify({
    chatEndpoint: d.apiExamples.chatEndpoint,
    nearbyPorts: nearby
  }, null, 2);

  UI.eventTimeline.innerHTML = d.events.map(ev => `
    <article class="tl-item">
      <time>${ev.time}</time>
      <strong>${ev.title}</strong>
      <p>${ev.description}</p>
    </article>
  `).join('');
}

function renderMetrics(container, rows) {
  container.innerHTML = rows.map(([label, value]) => `
    <div class="metric-card">
      <span>${label}</span>
      <strong>${value}</strong>
    </div>
  `).join('');
}

// HEALTH RING & KPIs
function setRing(score) {
  const circ = 301.59;
  const offset = circ - (score / 100) * circ;
  UI.healthRing.style.strokeDashoffset = offset;
  UI.healthRing.style.stroke = score >= 80
    ? 'var(--ok)'
    : score >= 60
      ? 'var(--warn)'
      : 'var(--err)';
}

function sparkline(container, values) {
  const W = 220, H = 44;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * W;
    const y = H - 4 - ((v - min) / range) * (H - 10);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const poly = pts.join(' ');
  const area = `${pts[0]} ${poly} ${W},${H} 0,${H}`;
  const uid = container.id || Math.random().toString(36).slice(2);

  container.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <defs>
        <linearGradient id="sg${uid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--acc)" stop-opacity=".32" />
          <stop offset="100%" stop-color="var(--acc)" stop-opacity="0" />
        </linearGradient>
      </defs>
      <polygon points="${area}" fill="url(#sg${uid})"></polygon>
      <polyline class="spark-line" points="${poly}"></polyline>
    </svg>`;
}

function setTrend(el, series) {
  const delta = last(series) - series[0];
  el.className = 'trend-badge';
  if (delta > 0.3) {
    el.textContent = '+' + fmt1(delta);
    el.classList.add('up');
  } else if (delta < -0.3) {
    el.textContent = fmt1(delta);
    el.classList.add('down');
  } else {
    el.textContent = 'stable';
    el.classList.add('flat');
  }
}

// MAP
function initMap() {
  if (APP.map) return;

  APP.map = L.map('map', {
    zoomControl: true,
    scrollWheelZoom: true
  }).setView([42.36, -71.03], 10);

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 18,
    attribution:
      '&copy; OpenStreetMap contributors &copy; CARTO'
  }).addTo(APP.map);

  APP.mapLayers.vessel = L.marker([0, 0], {
    title: 'Vessel position'
  }).addTo(APP.map);

  APP.mapLayers.waypoint = L.marker([0, 0], {
    title: 'Waypoint',
    opacity: 0.9
  }).addTo(APP.map);

  APP.mapLayers.route = L.polyline([], {
    color: '#29d1e5',
    weight: 2,
    dashArray: '5,4'
  }).addTo(APP.map);

  APP.mapLayers.ports = L.layerGroup().addTo(APP.map);
}

function renderMap(d) {
  if (!APP.map) return;

  const lat = d.navigation.position.latitude;
  const lon = d.navigation.position.longitude;

  const wLat = d.navigation.waypoint.latitude;
  const wLon = d.navigation.waypoint.longitude;

  APP.map.setView([lat, lon], 11);

  APP.mapLayers.vessel.setLatLng([lat, lon]).bindPopup(`${d.vessel.name}`);
  APP.mapLayers.waypoint.setLatLng([wLat, wLon]).bindPopup('Waypoint');
  APP.mapLayers.route.setLatLngs([[lat, lon], [wLat, wLon]]);

  APP.mapLayers.ports.clearLayers();

  const near = findNearbyPorts(d.navigation.position, 60);
  near.forEach(p => {
    const m = L.circleMarker([p.latitude, p.longitude], {
      radius: 6,
      color: '#f5b34c',
      weight: 2,
      fillColor: '#f5b34c',
      fillOpacity: 0.9
    }).addTo(APP.mapLayers.ports);

    const dist = distanceNm(lat, lon, p.latitude, p.longitude).toFixed(1);
    m.bindPopup(`<strong>${p.name}</strong><br/>Fuel: ${p.fuel ? 'yes' : 'no'}<br/>Distance: ${dist} nm`);
  });
}

// CHAT / COPILOT
function seedChat() {
  addBubble(
    'Hello! I am the maritime AI copilot. I can summarize the current vessel, show nearby fuel ports, help with emergencies, and switch context when you change vessels.',
    'ai'
  );
}

async function handleQuestion(question) {
  addBubble(question, 'user');
  const typing = addTyping();
  const answer = await getAnswer(question);
  typing.remove();
  addBubble(answer, 'ai');
}

function addBubble(text, role) {
  const el = document.createElement('article');
  el.className = 'bubble ' + role;
  el.textContent = text;
  UI.chatWindow.appendChild(el);
  UI.chatWindow.scrollTop = UI.chatWindow.scrollHeight;
  return el;
}

function addTyping() {
  const el = document.createElement('div');
  el.className = 'bubble ai typing';
  el.innerHTML = '<span></span><span></span><span></span>';
  UI.chatWindow.appendChild(el);
  UI.chatWindow.scrollTop = UI.chatWindow.scrollHeight;
  return el;
}

async function getAnswer(question) {
  if (APP.apiEndpoint) {
    try {
      const res = await fetch(APP.apiEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          telemetry: APP.telemetry,
          source: 'vessel-dashboard-v6'
        })
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      return data.answer || data.response || 'API responded without an answer field.';
    } catch (err) {
      return 'Could not reach external API (' + err.message + '). Local fallback: ' + localAnswer(question);
    }
  }

  await sleep(220);
  return localAnswer(question);
}

function localAnswer(q) {
  const t = APP.telemetry;
  if (!t) return 'Telemetry is not available yet.';

  const lo = q.toLowerCase();

  if (has(lo, ['man overboard', 'mob'])) {
    return 'Man overboard procedure: 1) Shout "Man overboard" and keep visual contact. 2) Press MOB on the plotter if available. 3) Throw flotation and mark position. 4) Turn to a recovery manoeuvre while avoiding propeller risk. 5) Call MAYDAY if the person is not immediately recovered.';
  }

  if (has(lo, ['fire', 'vessel fire', 'on board fire', 'onboard fire'])) {
    return 'Fire on board: 1) Raise the alarm and stop ventilation to the space if safe. 2) Inform crew and prepare for possible abandonment. 3) Use appropriate extinguisher (never water on electrical or fuel fires). 4) Navigate clear of traffic and call MAYDAY if fire is not under control. 5) Keep lifejackets and grab bag ready.';
  }

  if (has(lo, ['bilge', 'water in hull', 'taking on water'])) {
    return 'Water ingress: 1) Locate the source (through-hull, stuffing box, hull damage). 2) Start bilge pumps and use manual pumps if needed. 3) Reduce speed to limit water forcing in. 4) Prepare to beach the vessel in shallow water if flooding continues. 5) Call PAN-PAN or MAYDAY depending on severity.';
  }

  if (has(lo, ['lose propulsion', 'lost engine', 'no propulsion', 'engine failure'])) {
    return 'Loss of propulsion: 1) Drop anchor if depth allows and there is traffic or lee shore. 2) Diagnose engine (fuel, cooling, alarms). 3) Display appropriate signals and inform nearby traffic by VHF. 4) If drifting into danger, issue a PAN-PAN or MAYDAY as needed.';
  }

  if (has(lo, ['mayday', 'vhf distress', 'distress call'])) {
    return 'MAYDAY procedure on VHF Channel 16: say "MAYDAY, MAYDAY, MAYDAY" followed by vessel name and call sign, position (lat/long or bearing/distance), nature of distress, assistance required, number of people on board, and description of vessel. End with "OVER".';
  }

  if (has(lo, ['summary', 'status', 'overall'])) {
    return `Current vessel status: ${t.vessel.name} making ${fmt1(last(t.history.speedKn))} kn on heading ${fmt0(last(t.history.headingDeg))}°. Engine at ${fmt0(last(t.history.rpm))} rpm, fuel ${t.tanks.fuel.main.levelPercent}%, house battery ${fmt1(last(t.history.batteryVoltage))} V. Health index is ${t.analytics.maintenanceScore}/100 with ${t.analytics.alerts.length} active alerts.`;
  }

  if (has(lo, ['engine', 'propulsion'])) {
    const e = t.propulsion.engine1;
    return `Engine summary: ${e.rpm} rpm, load ${e.loadPercent}%, coolant ${fmt1(e.coolantTempC)} °C, oil pressure ${fmt0(e.oilPressureKpa)} kPa. Alternator at ${fmt1(t.energy.charging.alternatorVoltage)} V.`;
  }

  if (has(lo, ['battery', 'power', 'electrical', 'energy'])) {
    const b = t.energy.batteries.house;
    return `Power summary: house battery ${fmt1(b.voltage)} V at ${b.socPercent}% state of charge. Alternator voltage ${fmt1(t.energy.charging.alternatorVoltage)} V. Range estimate ${t.analytics.rangeEstimateNm} nm under current consumption.`;
  }

  if (has(lo, ['alert', 'alarm', 'risk'])) {
    if (!t.analytics.alerts.length) return 'There are no active alerts at the moment.';
    const top = t.analytics.alerts[0];
    return `Top priority alert: ${top.title} (${top.severity}). ${top.description} Recommended action: ${top.recommendation}`;
  }

  if (has(lo, ['port', 'fuel', 'marina', 'harbor', 'harbour'])) {
    const near = findNearbyPorts(t.navigation.position, 60);
    if (!near.length) return 'No ports with fuel found within 60 nautical miles.';
    const lines = near.slice(0, 4).map(p => {
      const dnm = distanceNm(
        t.navigation.position.latitude,
        t.navigation.position.longitude,
        p.latitude,
        p.longitude
      ).toFixed(1);
      return `${p.name} at ${dnm} nm (fuel ${p.fuel ? 'available' : 'not available'})`;
    });
    return 'Nearby ports with fuel:
' + lines.join('
');
  }

  return 'I could not map that question to a specific procedure. Try asking about status, engine, power, alerts, emergencies (MOB, fire, flooding, propulsion), or nearby fuel ports.';
}

// HELPERS
function last(arr) { return arr[arr.length - 1]; }
function fmt0(v) { return Math.round(v); }
function fmt1(v) { return Math.round(v * 10) / 10; }
function fmt4(v) { return Math.round(v * 10000) / 10000; }
function has(text, keys) { return keys.some(k => text.includes(k)); }
function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch { return iso; }
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Simple great-circle distance in nautical miles
function distanceNm(lat1, lon1, lat2, lon2) {
  const R = 6371; // km
  const toRad = x => x * Math.PI / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1))*Math.cos(toRad(lat2))*Math.sin(dLon/2)**2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  const dKm = R * c;
  return dKm * 0.539957; // km → nm
}

function findNearbyPorts(pos, radiusNm = 60) {
  if (!APP.ports || !APP.ports.length) return [];
  return APP.ports.filter(p =>
    distanceNm(pos.latitude, pos.longitude, p.latitude, p.longitude) <= radiusNm
  ).sort((a, b) =>
    distanceNm(pos.latitude, pos.longitude, a.latitude, a.longitude) -
    distanceNm(pos.latitude, pos.longitude, b.latitude, b.longitude)
  );
}
