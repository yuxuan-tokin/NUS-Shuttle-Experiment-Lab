const element = id => document.getElementById(id);
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
    if (now - lastPaint >= 40 || progress === 1) {
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
  const routeColors = ['#c6b8b0', '#d1c799', '#c8b7d9', '#baaccb', '#a8bccb', '#e2bd98', '#adc8aa'];
  Object.entries(setup.routes).forEach(([route, sequence], index) => {
    const congested = snapshot?.rule?.some(r => r.route === route && r.congested);
    const flash = (flashes.get(route) || 0) > Date.now();
    context.strokeStyle = flash ? '#08ad96' : congested ? '#e13848' : routeColors[index];
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
  for (const place of setup.places) {
    if (place.kind !== 'stop') continue;
    const point = project(place.xy);
    context.strokeStyle = '#647e89';
    context.lineWidth = 1.5;
    context.beginPath();
    context.arc(...point, 4, 0, Math.PI * 2);
    context.stroke();
    if (element('labels').checked) label(place.name, [point[0] + 6, point[1] - 5], '#58717e');
  }
  for (const entry of drawnPlaces) {
    if (!element('labels').checked && selectedPlace !== entry.destination) continue;
    for (const facility of entry.facilities) {
      const important = facility.standalone || facility.activity === 'study' || facility.id === 'ACT:DIN-TERRACE';
      if (!important && selectedPlace !== entry.destination) continue;
      const offset = facilityOffset(entry.destination, facility.activity === 'dining' ? 2 : 3);
      const point = [entry.point[0] + offset[0], entry.point[1] + offset[1]];
      if (facility.offcampus_food) {
        context.strokeStyle = '#be6c2277';
        context.setLineDash([3, 3]);
        context.beginPath();
        context.moveTo(...entry.point);
        context.lineTo(...point);
        context.stroke();
        context.setLineDash([]);
      }
      dot(point, 4, colors[facility.activity]);
      label(facility.name, [point[0] + 7, point[1] + 13], colors[facility.activity]);
    }
  }
  if (snapshot) {
    for (const bus of snapshot.buses) {
      const point = movingPoint(previousBuses.get(bus.id), ...bus.xy);
      context.fillStyle = bus.extra ? '#dc753a' : '#145a7b';
      context.fillRect(point[0] - 4, point[1] - 4, 9, 9);
      label(bus.route + ' ' + bus.load + '/88', [point[0] + 7, point[1] - 7]);
    }
  }
}

function showPlace(destination) {
  selectedPlace = destination;
  const place = setup.places[destination];
  const facilities = setup.facilities.filter(f => f.destination === destination);
  element('place-title').textContent = place.name;
  element('place-info').textContent = 'Coordinates ' + place.xy.map(n => n.toFixed(6)).join(', ') + '. ' +
    facilities.map(f => f.name + ' · ' + f.activity + ' · stops ' + f.candidate_stops.join(', ')).join(' | ');
  draw();
}

function show(value) {
  if (value.time < (snapshot?.time ?? 0)) { lastDispatchCount = 0; flashes.clear(); }
  const dispatches = value.dispatches || [];
  dispatches.slice(lastDispatchCount).forEach(d => flashes.set(d.route, Date.now()+1200));
  lastDispatchCount = dispatches.length;
  if (flashes.size) setTimeout(draw, 1250);
  snapshot = value;
  drawHourly(value.hourly || []);
  element('clock').textContent = clock(value.time);
  const metrics = value.metrics;
  const ev = value.evaluation;
  const cards = ev ? [
    ['Waiting · passenger-min', ev.total_wait?.toFixed(0)],
    ['Capacity failures · attempts %', ev.capacity_failure_pct?.toFixed(2) ?? '—'],
    ['Overall utilization · %', ev.overall_utilization_pct?.toFixed(2) ?? '—'],
    ['Extra buses', ev.extra_buses],
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
  const totals = value.activity_counts;
  element('activity-totals').textContent = totals ? `${totals.class_attending} in class · ${totals.dining} dining · ${totals.studying} studying · ${totals.queuing} queuing` : '';
  element('metrics').textContent = JSON.stringify(metrics, null, 2);
  element('observation').textContent = JSON.stringify(value.llm_input || value.observation, null, 2);
  element('decisions').textContent = JSON.stringify({decisions: value.decisions, rule: value.rule}, null, 2);
  draw();
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
  element('buildings').replaceChildren(...setup.places.filter(p => p.kind !== 'stop').map(p => {
    const tr=document.createElement('tr');
    [p.name,p.kind,p.building || '—',p.xy.map(n=>n.toFixed(6)).join(', ')].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td);});return tr;
  }));
  element('facilities').replaceChildren();
  for (const facility of setup.facilities) {
    const row = document.createElement('tr');
    for (const text of [facility.name, facility.standalone ? 'Standalone' : facility.building, facility.xy.map(n => n.toFixed(6)).join(', '), facility.candidate_stops.join(', ')]) {
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
element('fit').onclick = () => {zoom = 1; pan = [0, 0]; draw();};
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
  if (!drag) return;
  const dx = event.clientX - drag[0], dy = event.clientY - drag[1];
  if (Math.abs(dx) + Math.abs(dy) > 2) clicked = false;
  pan[0] += dx;
  pan[1] += dy;
  drag = [event.clientX, event.clientY];
  draw();
};
canvas.onpointerup = event => {
  drag = null;
  if (!clicked) return;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  const nearest = drawnPlaces.map(p => ({...p, distance: Math.hypot(p.point[0] - x, p.point[1] - y)})).sort((a, b) => a.distance - b.distance)[0];
  if (nearest && nearest.distance < 35) showPlace(nearest.destination);
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

function drawHourly(rows) {
  const root=element('hourly'); root.replaceChildren();
  for (const [key,title,color] of [['total_wait','Waiting · passenger-min','#426cb4'],['capacity_failure_pct','Capacity failure · %','#de4b62'],['overall_utilization_pct','Overall utilization · %','#149786']]) {
    const box=document.createElement('div'); const heading=document.createElement('small');heading.textContent=title;box.append(heading);
    const c=document.createElement('canvas');c.width=360;c.height=130;c.style.width='100%';box.append(c);root.append(box);
    const ctx=c.getContext('2d');const valid=rows.filter(r=>r[key]!=null);const max=Math.max(1,...valid.map(r=>r[key]));
    ctx.font='10px Segoe UI';ctx.fillStyle='#647885';ctx.fillText(max.toFixed(0),0,12);ctx.fillText('0',0,105);
    ctx.strokeStyle='#dae3e8';ctx.beginPath();ctx.moveTo(30,10);ctx.lineTo(30,105);ctx.lineTo(350,105);ctx.stroke();
    let active=false;ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();
    rows.forEach((r,i)=>{if(r[key]==null){active=false;return;}const x=35+i*310/Math.max(1,rows.length-1),y=105-r[key]/max*90;if(active)ctx.lineTo(x,y);else ctx.moveTo(x,y);active=true;});ctx.stroke();
    rows.forEach((r,i)=>{if(r[key]==null)return;const x=35+i*310/Math.max(1,rows.length-1),y=105-r[key]/max*90;ctx.fillStyle=color;ctx.beginPath();ctx.arc(x,y,2.5,0,Math.PI*2);ctx.fill();if(i%Math.max(1,Math.ceil(rows.length/5))===0){ctx.fillStyle='#647885';ctx.fillText(clock(r.start)+(r.end-r.start<60?'*':''),x-10,124);}});
    c.title=rows.map(r=>clock(r.start)+'–'+clock(r.end)+': '+(r[key]?.toFixed(2)??'no observations')).join('\n');
  }
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
  const hourly=element('hourly').parentElement;
  overlay('Metrics & hourly charts',[element('cards'),hourly],'metrics-overlay');
  overlay('Buildings & inspection',[document.querySelector('.inspect')],'inspection-overlay');
  const toolbar=document.querySelector('.toolbar');
  const pace=element('pace').closest('label');if(pace)pace.hidden=true;
  overlay('Map layers & legend',[toolbar,document.querySelector('.legend')],'layers-overlay',document.body);
  element('hourly').textContent='Run a simulation to view hourly charts.';
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==window.parent)return;
    if(event.data?.type==='nextbus-panels')document.body.classList.toggle('panels-hidden',Boolean(event.data.hidden));
  });
  window.parent.postMessage({type:'nextbus-map-ready'},location.origin);
}
