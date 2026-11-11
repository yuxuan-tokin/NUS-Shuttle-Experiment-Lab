const routeColors = {A2:'#2860b0', A1:'#be4a20', D2:'#087b68', D1:'#874eb0', K:'#9b6800', R1:'#a83965', R2:'#426374'};
const routeColor = route => routeColors[route] || '#374957';
let hoveredPlace = null;
let placePicker = null;
document.addEventListener('pointerdown', event => {
  if (placePicker && !placePicker.contains(event.target)) {
    placePicker.remove();
    placePicker = null;
  }
});
const busCapacity = () => snapshot?.service_charts?.capacity || runConfig?.capacity || 88;

function locatePlace(destination) {
  zoom = Math.max(zoom, 1.8);
  pan = [0, 0];
  const point = project(setup.places[destination].xy);
  pan = [canvas.clientWidth / 2 - point[0], canvas.clientHeight / 2 - point[1]];
  showPlace(destination);
}
let panelHost = null;
const element = id => document.getElementById(id) || panelHost?.querySelector('#' + id);
const panelQueryAll = selector => [...document.querySelectorAll(selector), ...(panelHost?.querySelectorAll(selector) || [])];
const panelQuery = selector => document.querySelector(selector) || panelHost?.querySelector(selector);
const canvas = element('map');
const context = canvas.getContext('2d');
const colors = {class: '#5079a8', dining: '#be6c22', study: '#8056b0', residence: '#307a71', queue: '#c44356', free: '#a3adb1'};
let setup, snapshot, runConfig, selectedPlace;
let running = false;
let busy = false;
let loopGeneration = 0;
let zoom = 1;
let pan = [0, 0];
let drag;
let clicked = false;
let drawnPlaces = [];
let lastDispatchCount = 0;
let flashes = new Map();
let motionSource, motionFrame = 0, motionProgress = 1;
const previousPeople = new Map(), previousBuses = new Map();

function movingPoint(previous, latitude, longitude) {
  if (!previous || motionProgress === 1) return project([latitude, longitude]);
  return project([
    previous[0] + (latitude - previous[0]) * motionProgress,
    previous[1] + (longitude - previous[1]) * motionProgress,
  ]);
}

function prepareMotion(frame, source, animate) {
  cancelAnimationFrame(motionFrame);
  const interpolate = animate && source && source === motionSource && snapshot &&
    frame.time > snapshot.time && !frame.map &&
    !matchMedia('(prefers-reduced-motion: reduce)').matches;
  motionSource = source;
  motionProgress = 1;
  previousPeople.clear();
  previousBuses.clear();
  if (!interpolate) return;
  for (const [id, latitude, longitude] of snapshot.people) {
    previousPeople.set(id, [latitude, longitude]);
  }
  for (const bus of snapshot.buses) previousBuses.set(bus.id, bus.xy);
  motionProgress = 0;
  const start = performance.now();
  let lastPaint = start;
  function paint(now) {
    const progress = Math.min(1, (now - start) / 320);
    if (now - lastPaint >= 1000 / 60 - 1 || progress === 1) {
      motionProgress = progress;
      draw();
      lastPaint = now;
    }
    if (progress < 1) motionFrame = requestAnimationFrame(paint);
    else {
      previousPeople.clear();
      previousBuses.clear();
    }
  }
  motionFrame = requestAnimationFrame(paint);
}

async function api(path, data) {
  if (!['http:', 'https:'].includes(location.protocol)) {
    throw Error('This is a file preview. Start run_simulator.ps1, then open http://127.0.0.1:8765 to use the simulator.');
  }
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(), data === undefined ? 15000 : 120000);
  const options = data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
  };
  try {
    const response = await fetch('./api/' + path, {...options, signal: abort.signal, cache: 'no-store'});
    if (!response.headers.get('content-type')?.includes('application/json')) {
      throw Error('The simulator server returned an unexpected page. Restart run_simulator.ps1 and reload this tab.');
    }
    const value = await response.json();
    if (!response.ok) throw Error(value.error || `Request failed (${response.status})`);
    return value;
  } catch (error) {
    if (error.name === 'AbortError') throw Error('The server did not respond in time. Check the simulator terminal, then reload this page.');
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function clock(minutes) {
  return String(Math.floor(minutes / 60)).padStart(2, '0') + ':' + String(Math.floor(minutes % 60)).padStart(2, '0');
}

function project(point) {
  const scale = Math.min(canvas.clientWidth / .019, canvas.clientHeight / .023) * zoom;
  return [canvas.clientWidth / 2 + (point[1] - 103.777) * scale + pan[0],
    canvas.clientHeight / 2 - (point[0] - 1.300) * scale + pan[1]];
}

function facilityOffset(destination, activity) {
  const food = setup.facilities.find(f => f.destination === destination && f.offcampus_food);
  return food && activity === 2 ? [30, -12] : [0, 0];
}

function dot(point, radius, color) {
  context.fillStyle = color;
  context.beginPath();
  context.arc(point[0], point[1], radius, 0, Math.PI * 2);
  context.fill();
}

function label(text, point, color = '#344e59') {
  context.font = '11px Segoe UI, sans-serif';
  context.lineWidth = 3;
  context.strokeStyle = '#ffffffdd';
  context.strokeText(text, point[0], point[1]);
  context.fillStyle = color;
  context.fillText(text, point[0], point[1]);
}

function draw() {
  const ratio = window.devicePixelRatio || 1;
  const width = Math.round(canvas.clientWidth * ratio);
  if (canvas.width !== width) canvas.width = width;
  const height = Math.round(canvas.clientHeight * ratio);
  if (canvas.height !== height) canvas.height = height;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width / ratio, height / ratio);
  if (!setup) return;

  Object.entries(setup.routes).forEach(([route, sequence], index) => {
    const congested = snapshot?.rule?.some(r => r.route === route && r.congested);
    const flash = (flashes.get(route) || 0) > Date.now();
    context.strokeStyle = congested ? '#e13848' : routeColor(route);
    context.lineWidth = flash ? 7 : congested ? 4 : 2;
    context.beginPath();
    sequence.forEach((stop, order) => {
      const point = project(setup.places[stop].xy);
      if (order) context.lineTo(...point);
      else context.moveTo(...point);
    });
    context.stroke();
  });
  drawnPlaces = [];
  for (const destination of setup.destinations) {
    const place = setup.places[destination];
    const point = project(place.xy);
    const facilities = setup.facilities.filter(f => f.destination === destination);
    const category = place.kind === 'residence' ? 'residence' : facilities.some(f => f.activity === 'study') ? 'study' : facilities.some(f => f.activity === 'dining') ? 'dining' : 'class';
    dot(point, facilities.length || category === 'residence' ? 4 : 2.5, colors[category] + '88');
    drawnPlaces.push({destination, point, facilities});
  }
  if (snapshot && element('people').checked) {
    for (const person of snapshot.people) {
      const [id, latitude, longitude, state, activity, destination] = person;
      if (element('transit-only').checked && state !== 2 && state !== 3) continue;
      const point = movingPoint(previousPeople.get(id), latitude, longitude);
      const color = state === 2 ? colors.queue : state === 1 || state === 3 ? '#5d778988' : [colors.free, colors.class, colors.dining, colors.study, colors.residence][activity];
      if (state === 0 || state === 2 || state === 4 || state === 6) {
        const radius = Math.sqrt((id * 37 % 101) / 101) * (state === 2 ? 8 : 11);
        const angle = id * 2.399963;
        const offset = state === 4 ? facilityOffset(destination, activity) : [0, 0];
        point[0] += Math.cos(angle) * radius + offset[0];
        point[1] += Math.sin(angle) * radius + offset[1];
      }
      context.fillStyle = color;
      context.fillRect(point[0], point[1], 1.8, 1.8);
    }
  }
  for (const [destination, place] of setup.places.entries()) {
    if (place.kind !== 'stop') continue;
    const point = project(place.xy);
    drawnPlaces.push({destination, point, facilities:[]});
    context.strokeStyle = '#647e89';
    context.lineWidth = 1.5;
    context.beginPath();
    context.arc(...point, 4, 0, Math.PI * 2);
    context.stroke();
  }
  const activePlace = hoveredPlace ?? selectedPlace;
  const occupiedLabels = [];
  if (activePlace != null && setup.places[activePlace]) {
    const point = project(setup.places[activePlace].xy);
    occupiedLabels.push({x:point[0]+12, y:point[1]-12, width:setup.places[activePlace].name.length*7});
  }
  for (const entry of drawnPlaces) {
    if (!element('labels').checked || entry.destination === activePlace) continue;
    const place = setup.places[entry.destination];
    if (place.kind !== 'stop' && !entry.facilities.length) continue;
    const box = {x:entry.point[0]+7, y:entry.point[1]-5, width:place.name.length*7};
    if (occupiedLabels.some(other => Math.abs(box.y-other.y)<16 && box.x<other.x+other.width && other.x<box.x+box.width)) continue;
    occupiedLabels.push(box);
    label(place.name, [box.x, box.y]);
  }
  for (const destination of [activePlace]) {
    if (destination == null || !setup.places[destination]) continue;
    const place = setup.places[destination];
    const point = project(place.xy);
    dot(point, 7, '#263f50');
    label(place.name, [point[0] + 12, point[1] - 12]);
  }
  if (snapshot) {
    for (const bus of snapshot.buses) {
      const point = movingPoint(previousBuses.get(bus.id), ...bus.xy);
      const loadShare = bus.load / busCapacity();
      context.fillStyle = loadShare >= 0.75 ? '#c43d49' : loadShare > 0.25 ? '#d28a16' : '#21845b';
      context.fillRect(point[0] - 4, point[1] - 4, 9, 9);
      label(bus.route + ' ' + bus.load + '/' + busCapacity(), [point[0] + 7, point[1] - 7]);
    }
  }
}

function showPlace(destination) {
  placePicker?.remove();
  placePicker = null;
  selectedPlace = destination;
  panelQueryAll('#buildings tr, #facilities tr').forEach(row => {
    row.classList.toggle('selected-place', Number(row.dataset.destination) === destination);
  });
  const place = setup.places[destination];
  const facilities = setup.facilities.filter(f => f.destination === destination);
  element('place-title').textContent = place.name;
  const stops = facilities.length
    ? facilities.flatMap(f => f.candidate_stops)
    : (place.stops || []).map(stop => setup.places[stop].name);
  const coordinates = document.createElement('div');
  coordinates.textContent = 'Coordinates ' + place.xy.map(n => n.toFixed(6)).join(', ');
  const building = document.createElement('div');
  building.textContent = 'Building ' + (place.building || 'Not specified');
  const candidates = document.createElement('div');
  candidates.textContent = 'Candidate stops ' + ([...new Set(stops)].join(', ') || 'None');
  element('place-info').replaceChildren(building, coordinates, candidates);
  draw();
}

function show(value) {
  element('rule-indicator-note').textContent = value.rule_description || 'Red routes show the recorded crowding-condition indicator. Older replays may use the BASE1 reference rule. A pending extra bus can still prevent dispatch.';
  if (value.time < (snapshot?.time ?? 0)) { lastDispatchCount = 0; flashes.clear(); }
  const dispatches = value.dispatches || [];
  dispatches.slice(lastDispatchCount).forEach(d => flashes.set(d.route, Date.now()+1200));
  lastDispatchCount = dispatches.length;
  if (flashes.size) setTimeout(draw, 1250);
  snapshot = value;
  element('clock').textContent = clock(value.time);
  const metrics = value.metrics;
  drawServiceMetrics(value);
  const totals = value.activity_counts;
  element('activity-totals').textContent = totals ? `${totals.class_attending} in class · ${totals.dining} dining · ${totals.studying} studying · ${totals.queuing} queuing` : '';
  element('metrics').textContent = JSON.stringify(metrics, null, 2);
  element('observation').textContent = JSON.stringify(value.llm_input || value.observation, null, 2);
  element('decisions').textContent = JSON.stringify({decisions: value.decisions, rule: value.rule}, null, 2);
  draw();
}

function drawMetricCards(ev, metrics) {
  const cards = ev ? [
    ['Waiting · passenger-min', ev.total_wait?.toFixed(0)],
    ['Capacity failures · attempts %', ev.capacity_failure_pct?.toFixed(2) ?? '—'],
    ['Overall utilization · %', ev.overall_utilization_pct?.toFixed(2) ?? '—'],
    ['Extra bus runs', ev.extra_buses],
    ['Extra service · bus-hours', ev.extra_bus_hours?.toFixed(2)]
  ] : [['Waiting · passenger-min', metrics.waiting_passenger_minutes.toFixed(0)]];
  element('cards').replaceChildren(...cards.map(([title, value]) => {
    const box = document.createElement('div');
    box.className = 'card';
    const small = document.createElement('small');
    small.textContent = title;
    const strong = document.createElement('strong');
    strong.textContent = value;
    box.append(small, strong);
    return box;
  }));
}

async function action(fn) {
  if (busy) return;
  busy = true;
  updateControls();
  element('error').textContent = '';
  element('run-status').textContent = 'Computing';
  try {
    await fn();
    element('run-status').textContent = finished() ? 'Finished' : running ? 'Running' : snapshot ? 'Paused' : 'Ready to configure';
  } catch (error) {
    element('error').textContent = error.message;
    running = false;
    element('play').textContent = 'Run';
    element('run-status').textContent = 'Needs attention';
  } finally {
    busy = false;
    updateControls();
  }
}

function finished() {
  return snapshot && runConfig && snapshot.time >= runConfig.end;
}

function updateControls() {
  element('reset').disabled = busy || !setup;
  element('apply').disabled = busy || !setup;
  element('step').disabled = busy || running || !snapshot || finished();
  element('export').disabled = busy || !snapshot;
  element('play').disabled = !snapshot || finished() || (busy && !running);
}

function showFacilities() {
  panelQueryAll('.legend .route-legend').forEach(node => node.remove());
  for (const route of Object.keys(setup.routes)) {
    const entry = document.createElement('span');
    entry.className = 'route-legend';
    const symbol = document.createElement('i');
    symbol.className = 'route-symbol';
    symbol.style.background = routeColor(route);
    entry.append(symbol, document.createTextNode(route));
    panelQuery('.legend').append(entry);
  }
  element('buildings').replaceChildren(...setup.destinations.map(destination => setup.places[destination]).map(p => {
    const tr=document.createElement('tr');
    tr.dataset.destination = setup.places.indexOf(p);
    tr.classList.toggle('selected-place', setup.places.indexOf(p) === selectedPlace);
    tr.tabIndex = 0;
    tr.style.cursor = 'pointer';
    tr.onclick = () => locatePlace(setup.places.indexOf(p));
    tr.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); tr.click(); } };
    [p.name,p.kind,p.xy.map(n=>n.toFixed(6)).join(', ')].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td);});return tr;
  }));
  element('facilities').replaceChildren();
  for (const facility of new Map(setup.facilities.map(f => [f.destination, f])).values()) {
    const row = document.createElement('tr');
    row.dataset.destination = facility.destination;
    row.classList.toggle('selected-place', facility.destination === selectedPlace);
    row.tabIndex = 0;
    row.style.cursor = 'pointer';
    row.onclick = () => locatePlace(facility.destination);
    row.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); row.click(); } };
    for (const text of [facility.building, facility.xy.map(n => n.toFixed(6)).join(', '), facility.candidate_stops.join(', ')]) {
      const cell = document.createElement('td');
      cell.textContent = text;
      row.append(cell);
    }
    element('facilities').append(row);
  }
}

function showRules(config) {
  element('study-rules').textContent = 'General buildings and canteens decrease at ' + clock(config.study_evening_starts) + ' and close at ' + clock(config.general_study_closes) + '. Residence study is limited to the assigned home. Libraries follow their own opening hours.';
}

element('reset').onclick = () => action(async () => {
  running = false;
  loopGeneration++;
  const config = JSON.parse(element('config').value);
  config.day = element('day').value;
  config.arm = element('arm').value;
  config.population = Number(element('population').value);
  config.seed = Number(element('seed').value);
  const result = await api('reset', config);
  runConfig = config;
  if (result.map) setup = {...setup, ...result.map};
  showFacilities();
  lastDispatchCount=0; flashes.clear();
  show(result);
  showRules(config);
  element('play').textContent = 'Run';
});
element('apply').onclick = () => action(async () => {
  const config = JSON.parse(element('config').value);
  element('day').value = config.day;
  element('arm').value = config.arm;
  element('population').value = config.population;
  element('seed').value = config.seed;
  showRules(config);
});
element('step').onclick = () => action(async () => show(await api('step', {minutes: 5})));
element('play').onclick = () => {
  running = !running;
  const generation = ++loopGeneration;
  element('play').textContent = running ? 'Pause' : 'Run';
  element('run-status').textContent = running ? 'Running' : busy ? 'Pausing after current update' : 'Paused';
  updateControls();
  if (running) loop(generation);
};
async function loop(generation) {
  if (!running || generation !== loopGeneration) return;
  await action(async () => show(await api('step', {minutes: Number(element('pace').value)})));
  if (finished()) {
    running = false;
    element('play').textContent = 'Run';
    element('run-status').textContent = 'Finished';
  }
  updateControls();
  if (running && generation === loopGeneration) setTimeout(() => loop(generation), 60);
}
element('export').onclick = () => action(async () => {
  show(await api('export', {}));
  element('error').textContent = 'Saved to outputs/ui_run';
});
function resetMapView() {
  zoom = 1;
  pan = [0, 0];
  draw();
}
element('fit').onclick = resetMapView;
element('people').onchange = draw;
element('transit-only').onchange = draw;
element('labels').onchange = draw;
canvas.onwheel = event => {
  event.preventDefault();
  zoom = Math.max(.5, Math.min(6, zoom * (event.deltaY < 0 ? 1.1 : .9)));
  draw();
};
canvas.onpointerdown = event => {
  drag = [event.clientX, event.clientY];
  clicked = true;
  canvas.setPointerCapture(event.pointerId);
};
canvas.onpointermove = event => {
  if (!drag) {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    const nearest = drawnPlaces.map(p => ({...p, distance: Math.hypot(p.point[0]-x, p.point[1]-y)})).sort((a,b) => a.distance-b.distance)[0];
    hoveredPlace = nearest?.distance < 12 ? nearest.destination : null;
    canvas.title = hoveredPlace == null ? '' : setup.places[hoveredPlace].name;
    draw();
    return;
  }
  const dx = event.clientX - drag[0], dy = event.clientY - drag[1];
  if (Math.abs(dx) + Math.abs(dy) > 2) clicked = false;
  pan[0] += dx;
  pan[1] += dy;
  drag = [event.clientX, event.clientY];
  draw();
};
canvas.onpointerleave = () => { hoveredPlace = null; canvas.title = ''; draw(); };
canvas.onpointerup = event => {
  drag = null;
  if (!clicked) return;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  const nearest = drawnPlaces.map(p => ({...p, distance: Math.hypot(p.point[0] - x, p.point[1] - y)})).sort((a, b) => a.distance - b.distance)[0];
  placePicker?.remove();
  placePicker = null;
  if (!nearest || nearest.distance >= 35) return;
  const overlapping = drawnPlaces.filter(p => Math.hypot(p.point[0]-nearest.point[0], p.point[1]-nearest.point[1]) < 12);
  if (overlapping.length === 1) { showPlace(nearest.destination); return; }
  placePicker = document.createElement('div');
  placePicker.className = 'place-picker';
  placePicker.setAttribute('role', 'group');
  placePicker.setAttribute('aria-label', 'Choose a location');
  for (const entry of overlapping) {
    const button = document.createElement('button');
    button.textContent = setup.places[entry.destination].name;
    button.onclick = () => showPlace(entry.destination);
    placePicker.append(button);
  }
  placePicker.onkeydown = event => {
    if (event.key === 'Escape') { placePicker?.remove(); placePicker = null; }
  };
  document.body.append(placePicker);
  placePicker.style.left = Math.max(8, Math.min(event.clientX, innerWidth-placePicker.offsetWidth-8))+'px';
  placePicker.style.top = Math.max(8, Math.min(event.clientY, innerHeight-placePicker.offsetHeight-8))+'px';
  placePicker.firstElementChild.focus();
};
window.onresize = draw;

api('setup').then(value => {
  setup = value;
  element('config').value = JSON.stringify(value.config, null, 2);
  for (const day of value.days) {
    const option = document.createElement('option');
    option.value = day;
    option.textContent = day;
    element('day').append(option);
  }
  element('day').value = value.config.day;
  if (!element('day').value) element('day').selectedIndex = 0;
  element('population').value = value.config.population;
  element('arm').value = value.config.arm;
  element('seed').value = value.config.seed;
  showRules(value.config);
  for (const warning of value.warnings) {
    const item = document.createElement('li');
    item.textContent = warning;
    element('warnings').append(item);
  }
  showFacilities();
  element('boot-status').hidden = true;
  element('run-status').textContent = 'Ready to configure';
  updateControls();
  draw();
}).catch(error => {
  element('boot-status').textContent = error.message;
  element('run-status').textContent = 'Unable to load';
});

let metricScope = 'Overall';
const metricRoutes = ['A2', 'A1', 'D2', 'D1'];


function metricDifference(before, after, capacity) {
  const change = key => after[key] - before[key];
  const percent = (value, total) => total > 0 ? 100 * value / total : null;
  return {
    total_wait: change('wait'),
    capacity_failure_pct: percent(change('denials'), change('attempts')),
    overall_utilization_pct: percent(change('passengers'), capacity * change('service')),
    extra_buses: change('dispatches'),
    extra_bus_hours: change('extra_service') / 60,
    average_bus_load: change('service') > 0 ? change('passengers') / change('service') : null,
  };
}

function drawServiceMetrics(frame) {
  const experiment = frame.experiment || {name:'Interactive preview', day:runConfig?.day || setup?.config?.day || ''};
  const contextLines = [experiment.name, experiment.day + ' · ' + clock(frame.time),
    experiment.start == null ? '' : 'Window ' + clock(experiment.start) + '–' + clock(experiment.end)];
  element('metric-context').textContent = contextLines.filter(Boolean).join('\n');
  element('export-metrics').disabled = false;
  const data = frame.service_charts;
  const routes = Object.keys(setup?.routes || {});
  const scopes = ['Overall', ...metricRoutes.filter(route => routes.includes(route)),
    ...routes.filter(route => !metricRoutes.includes(route))];
  if (!scopes.includes(metricScope)) metricScope = 'Overall';
  const group = element('metric-scope');
  if (group.dataset.scopes !== scopes.join(',')) {
    group.dataset.scopes = scopes.join(',');
    group.replaceChildren(...scopes.map((scope, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = scope;
      button.title = scope;
      button.setAttribute('aria-label', scope);
      button.style.setProperty('--scope-color', routeColor(scope));
      button.addEventListener('click', () => {
        metricScope = scope;
        if (snapshot) drawServiceMetrics(snapshot);
      });
      return button;
    }));
  }
  [...group.children].forEach((button, index) => {
    button.disabled = false;
    button.setAttribute('aria-pressed', String(scopes[index] === metricScope));
  });
  element('metric-scope-label').textContent = metricScope;
  const interval = element('metric-interval');
  interval.disabled = !data;
  const chart = element('extra-run-chart');
  if (!data && metricScope !== 'Overall') {
    element('cards').replaceChildren();
    element('hourly').textContent = 'This saved run has no route metrics. Rerun the experiment to view this route.';
    chart.replaceChildren();
    return;
  }
  if (!data) {
    drawMetricCards(frame.evaluation, frame.metrics);
    drawServiceIntervals((frame.hourly || []).map(row => ({...row,
      average_bus_load: row.overall_utilization_pct == null ? null : row.overall_utilization_pct * busCapacity() / 100,
    })), 60);
    chart.textContent = frame.evaluation ? 'Route and extra-run charts are available for new experiment runs.' : '';
    return;
  }
  const {timeline, capacity, start, end} = data;
  const minutes = interval.value === 'auto' ? (end - start <= 180 ? 15 : 60) : Number(interval.value);
  const scoped = point => metricScope === 'Overall' ? point.overall : point.routes[metricScope];
  const first = timeline[0];
  const last = timeline.at(-1);
  const rows = [];
  let opening = first;
  for (const point of timeline.slice(1)) {
    if (point.time - opening.time >= minutes || point === last) {
      rows.push({start: opening.time, end: point.time, ...metricDifference(scoped(opening), scoped(point), capacity)});
      opening = point;
    }
  }
  drawMetricCards(metricDifference(scoped(first), scoped(last), capacity), frame.metrics);
  drawServiceIntervals(rows, minutes, {start, end:last.time});
  drawExtraRuns(chart, data, frame.dispatches || [], last.time, rows, minutes);
}

function intervalTicks(rows, minutes) {
  const duration = Math.max(1, (rows.at(-1)?.end ?? 0) - (rows[0]?.start ?? 0));
  // Double tick spacing only when labels would be less than 60 SVG pixels apart.
  const stride = 2 ** Math.max(0, Math.ceil(Math.log2(duration * 60 / (280 * minutes))));
  return rows.filter((row, index) => index % stride === 0)
    .map(row => ({time:row.start, label:clock(row.start)+(row.end-row.start < minutes ? '*' : '')}));
}

function drawServiceIntervals(rows, minutes, range = {start:rows[0]?.start ?? 0, end:rows.at(-1)?.end ?? 1}) {
  const root = element('hourly');
  root.replaceChildren();
  const svgNode = (tag, attributes, content) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (content != null) node.textContent = content;
    return node;
  };
  for (const [key, title] of [
    ['total_wait', 'Waiting · passenger-min'],
    ['capacity_failure_pct', 'Capacity failure · %'],
    ['overall_utilization_pct', 'Utilization · %'],
    ['average_bus_load', 'Average onboard load · passengers per bus'],
  ]) {
    const box = document.createElement('div');
    const heading = document.createElement('small');
    heading.textContent = title;
    const svg = svgNode('svg', {viewBox:'0 0 360 145', role:'img', 'aria-label':title});
    box.append(heading, svg);
    root.append(box);
    const max = Math.max(1, ...rows.map(row => row[key] ?? 0));
    svg.append(svgNode('path', {d:'M 38 12 V 108 H 340', fill:'none', stroke:'#cbd3d9'}));
    for (const fraction of [0, 0.5, 1]) {
      svg.append(svgNode('text', {x:32, y:112-fraction*96, 'text-anchor':'end'}, (max*fraction).toFixed(0)));
    }
    const x = time => 48 + 280 * (time-range.start) / Math.max(1, range.end-range.start);
    let path = '';
    let active = false;
    rows.forEach((row, index) => {
      if (row[key] == null) { active = false; return; }
      const y = 108 - row[key] / max * 96;
      path += (active ? ' L ' : ' M ') + x(row.start) + ' ' + y;
      active = true;
    });
    svg.append(svgNode('path', {d:path, fill:'none', stroke:'#374957', 'stroke-width':1.8}));
    rows.forEach((row, index) => {
      const partial = row.end-row.start < minutes;
      if (row[key] != null) {
        const point = svgNode('circle', {cx:x(row.start), cy:108-row[key]/max*96, r:3, fill:'#374957'});
        point.append(svgNode('title', {}, clock(row.start)+'–'+clock(row.end)+
          (partial ? ' (partial)' : '')+' · '+row[key].toFixed(2)));
        svg.append(point);
      }
    });
    for (const tick of intervalTicks(rows, minutes)) {
      svg.append(svgNode('text', {x:x(tick.time), y:132, 'text-anchor':'middle'}, tick.label));
    }
  }
}

function drawExtraRuns(root, data, dispatches, now, rows, minutes) {
  const svgElement = (tag, attributes, content) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    if (content) node.textContent = content;
    return node;
  };
  const heading = document.createElement('small');
  heading.textContent = 'Extra bus run utilization · %';
  const svg = svgElement('svg', {viewBox: '0 0 360 145', role: 'img', 'aria-label': heading.textContent});
  root.replaceChildren(heading, svg);
  const x = time => 48 + 280 * (time - data.start) / Math.max(1, now - data.start);
  const y = load => 108 - 96 * load / data.capacity;
  svg.append(svgElement('path', {d:'M 38 12 V 108 H 340', fill:'none', stroke:'#cbd3d9'}));
  for (const percentage of [0, 50, 100]) {
    const level = y(percentage * data.capacity / 100);
    svg.append(svgElement('text', {x:32, y:level+4, 'text-anchor':'end'}, String(percentage)));
  }
  for (const tick of intervalTicks(rows, minutes)) {
    svg.append(svgElement('text', {x:x(tick.time), y:132, 'text-anchor':'middle'}, tick.label));
  }
  const visible = dispatches.filter(run => metricScope === 'Overall' || run.route === metricScope);
  if (!visible.length) svg.append(svgElement('text', {x:188, y:64, 'text-anchor':'middle'}, 'No extra bus runs dispatched'));
  for (const run of visible) {
    const color = routeColor(run.route);
    const series = data.extra_runs[run.bus_id];
    const group = svgElement('g', {});
    group.append(svgElement('title', {}, run.route + ' · dispatched ' + clock(run.time)));
    const marker = svgElement('circle', {cx: x(run.time), cy:108, r:3, fill:color});
    group.append(marker);
    if (series?.points.length) {
      const points = series.points;
      let [time, load] = points[0];
      let path = `M ${x(time)} ${y(load)}`;
      for (const [nextTime, nextLoad] of points.slice(1)) {
        path += ` H ${x(nextTime)} V ${y(nextLoad)}`;
      }
      path += ` H ${x(series.end ?? now)}`;
      group.append(svgElement('path', {d:path, fill:'none', stroke:color, 'stroke-width':1.8}));
    }
    svg.append(group);
  }
}

element('metric-interval').addEventListener('change', () => {
  if (snapshot) drawServiceMetrics(snapshot);
});

element('export-metrics').addEventListener('click', async () => {
  if (!snapshot) return;
  const button = element('export-metrics');
  button.disabled = true;
  element('metric-export-status').textContent = 'Preparing image…';
  // Capture the displayed frame before awaiting image decoding during playback.
  const metadata = element('metric-context').textContent;
  const scope = metricScope;
  const selectedInterval = element('metric-interval').value;
  const chartData = snapshot.service_charts;
  const interval = selectedInterval === 'auto' && chartData
    ? 'Auto (' + (chartData.end-chartData.start <= 180 ? '15 min' : '1 hour') + ')'
    : element('metric-interval').selectedOptions[0].textContent;
  const cards = [...element('cards').children].map(card => ({
    title:card.querySelector('small').textContent, value:card.querySelector('strong').textContent,
  }));
  const charts = [...panelQueryAll('#hourly svg, #extra-run-chart svg')].map(svg => {
    const copy = svg.cloneNode(true);
    copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    copy.setAttribute('width', '360');
    copy.setAttribute('height', '145');
    copy.querySelectorAll('text').forEach(text => {
      text.setAttribute('fill', '#52616c');
      text.setAttribute('font-family', 'Arial, sans-serif');
      text.setAttribute('font-size', '9');
    });
    return {title:svg.parentElement.querySelector('small').textContent,
      source:new XMLSerializer().serializeToString(copy)};
  });
  const notices = charts.length ? '' : element('hourly').textContent;
  const filename = ((snapshot.experiment?.name || 'preview') + '_' +
    (snapshot.experiment?.day || runConfig?.day || '') + '_' + clock(snapshot.time) + '_' + scope)
    .replace(/[^a-zA-Z0-9_-]/g, '_') + '.png';
  try {
    const output = document.createElement('canvas');
    const width = 760;
    output.width = width * 2;
    output.height = (580 + charts.length * 330 + metadata.length * 2) * 2;
    const ctx = output.getContext('2d');
    ctx.scale(2, 2);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, width, output.height / 2);
    let y = 42;
    const write = (text, font, color = '#253e4c') => {
      ctx.font = font;
      ctx.fillStyle = color;
      for (const paragraph of text.split('\n')) {
        let line = '';
        for (const word of paragraph.split(' ')) {
          if (line && ctx.measureText(line + ' ' + word).width > width - 64) {
            ctx.fillText(line, 32, y); y += 25; line = word;
          } else line += (line ? ' ' : '') + word;
        }
        ctx.fillText(line, 32, y); y += 25;
      }
    };
    const [name, ...details] = metadata.split('\n');
    write(name, 'bold 26px Arial');
    y += 10;
    write(details.join('\n'), '16px Arial', '#52616c');
    write(scope + ' · Interval ' + interval, '16px Arial', '#52616c');
    y += 16;
    for (let index=0; index<cards.length; index++) {
      const card = cards[index];
      const left = 32 + (index % 2) * 356;
      const top = y + Math.floor(index / 2) * 82;
      ctx.fillStyle = '#f0f4f6';
      ctx.fillRect(left, top, 340, 70);
      ctx.fillStyle = '#52616c';
      ctx.font = '14px Arial';
      ctx.fillText(card.title, left+14, top+23);
      ctx.fillStyle = '#253e4c';
      ctx.font = 'bold 24px Arial';
      ctx.fillText(card.value, left+14, top+54);
    }
    y += Math.ceil(cards.length / 2) * 82 + 12;
    if (notices) write(notices, '15px Arial');
    for (const chart of charts) {
      y += 12;
      write(chart.title, 'bold 20px Arial');
      const url = URL.createObjectURL(new Blob([chart.source], {type:'image/svg+xml'}));
      try {
        const picture = new Image();
        picture.src = url;
        await picture.decode();
        ctx.drawImage(picture, 20, y, 720, 290);
        y += 290;
      } finally { URL.revokeObjectURL(url); }
    }
    const cropped = document.createElement('canvas');
    cropped.width = output.width;
    cropped.height = Math.ceil(y + 16) * 2;
    cropped.getContext('2d').drawImage(output, 0, 0);
    const blob = await new Promise(resolve => cropped.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('Image export failed');
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    element('metric-export-status').textContent = 'Image exported';
  } catch (error) {
    element('metric-export-status').textContent = error.message;
  } finally { button.disabled = false; }
});

async function exportMapImage() {
  if (!snapshot) throw new Error('Select an experiment or preview first');
  const metadata = element('metric-context').textContent;
  const filename = ((snapshot.experiment?.name || 'preview') + '_' +
    (snapshot.experiment?.day || runConfig?.day || '') + '_' + clock(snapshot.time) + '_map')
    .replace(/[^a-zA-Z0-9_-]/g, '_') + '.png';
  const labels = element('labels').checked;
  const progress = motionProgress;
  const map = document.createElement('canvas');
  try {
    element('labels').checked = true;
    motionProgress = 1;
    draw();
    map.width = canvas.width;
    map.height = canvas.height;
    map.getContext('2d').drawImage(canvas, 0, 0);
  } finally {
    element('labels').checked = labels;
    motionProgress = progress;
    draw();
  }
  const legends = [...panelQueryAll('.legend span')].filter(span => span.querySelector('i'))
    .map(span => ({name:span.textContent, color:getComputedStyle(span.querySelector('i')).backgroundColor,
      bus:span.querySelector('i').classList.contains('bus-symbol'),
      route:span.querySelector('i').classList.contains('route-symbol')}));
  const width = Math.max(1000, canvas.clientWidth);
  const mapHeight = map.height * (width-64) / map.width;
  const output = document.createElement('canvas');
  output.width = width*2;
  output.height = Math.ceil(mapHeight+500+metadata.length*2)*2;
  const ctx = output.getContext('2d');
  ctx.scale(2,2);
  ctx.fillStyle = 'white';
  ctx.fillRect(0,0,width,output.height/2);
  let y = 42;
  for (const [index, paragraph] of metadata.split('\n').entries()) {
    ctx.font = index === 0 ? 'bold 26px Arial' : '16px Arial';
    ctx.fillStyle = index === 0 ? '#253e4c' : '#52616c';
    let line = '';
    for (const word of paragraph.split(' ')) {
      if (line && ctx.measureText(line+' '+word).width > width-64) {
        ctx.fillText(line,32,y); y+=32; line=word;
      } else line += (line ? ' ' : '')+word;
    }
    ctx.fillText(line,32,y); y+=32;
  }
  y+=8;
  ctx.fillStyle='#f7faf8';
  ctx.fillRect(32,y,width-64,mapHeight);
  ctx.drawImage(map,32,y,width-64,mapHeight);
  y+=mapHeight+32;
  let x=32;
  ctx.font='15px Arial';
  for (const item of legends) {
    const itemWidth=ctx.measureText(item.name).width+48;
    if (x+itemWidth>width-32) { x=32; y+=30; }
    ctx.fillStyle=item.color;
    if (item.bus || item.route) ctx.fillRect(x,y-11,item.route ? 18 : 10,item.route ? 3 : 10);
    else { ctx.beginPath(); ctx.arc(x+5,y-6,5,0,Math.PI*2); ctx.fill(); }
    ctx.fillStyle='#253e4c'; ctx.fillText(item.name,x+24,y);
    x+=itemWidth;
  }
  const cropped=document.createElement('canvas');
  cropped.width=output.width; cropped.height=Math.ceil(y+28)*2;
  cropped.getContext('2d').drawImage(output,0,0);
  const blob=await new Promise(resolve=>cropped.toBlob(resolve,'image/png'));
  if (!blob) throw new Error('Map export failed');
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a'); link.href=url; link.download=filename; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

if(new URLSearchParams(location.search).has('viewer')) {
  document.body.classList.add('viewer');
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin || event.data?.type!=='nextbus-frame' || !setup)return;
    if(event.data.frame.map){setup={...setup,...event.data.frame.map};showFacilities();}
    prepareMotion(event.data.frame, event.data.motion, event.data.animate);
    show(event.data.frame);
  });
}

// Map-view is an edge-to-edge canvas with independently collapsible overlays.
if(new URLSearchParams(location.search).has('viewer')) {

  function overlay(title,nodes,className,parent=document.body){
    const panel=document.createElement('details');panel.className='floating-panel '+className;
    const summary=document.createElement('summary');summary.textContent=title;
    const content=document.createElement('div');content.className='panel-content';
    nodes.forEach(node=>content.append(node));panel.append(summary,content);parent.append(panel);return panel;
  }
  overlay('Metrics & charts',[element('service-metrics')],'metrics-overlay');
  overlay('Buildings & inspection',[document.querySelector('.inspect')],'inspection-overlay');
  const toolbar=document.querySelector('.toolbar');
  const pace=element('pace').closest('label');if(pace)pace.hidden=true;
  element('fit').remove();
  overlay('Map layers & legend',[toolbar,document.querySelector('.legend'),element('rule-indicator-note'),element('map-navigation-hint')],'layers-overlay',document.body);
  if(window.parent!==window && window.parent.location.origin===location.origin) {
    window.parent.document.getElementById('map-panel-host')?.remove();
    panelHost=window.parent.document.createElement('div');
    panelHost.id='map-panel-host';panelHost.className='viewer';
    panelHost.style.display='contents';
    document.querySelectorAll('.floating-panel').forEach(panel=>panelHost.append(panel));
    window.parent.document.body.append(panelHost);
  }
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==window.parent)return;
    if(event.data?.type==='nextbus-export-map') {
      exportMapImage().then(
        () => window.parent.postMessage({type:'nextbus-map-exported',message:'Map exported'},location.origin),
        error => window.parent.postMessage({type:'nextbus-map-exported',message:error.message},location.origin));
    }
    if(event.data?.type==='nextbus-reset-view')resetMapView();
    if(event.data?.type==='nextbus-panels')document.body.classList.toggle('panels-hidden',Boolean(event.data.hidden));
  });
  window.parent.postMessage({type:'nextbus-map-ready'},location.origin);
}
