const $=id=>document.getElementById(id);
let state, selectedId='', replayId=null, followLive=true, lastFrame='', playing=false, polling=false;
let workspaceMap, previewSelected=false;
let serviceTimes=[];
const time=n=>`${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
const minutes=s=>{const [h,m]=s.split(':').map(Number);return h*60+m;};
const fmt=n=>n==null?'—':Number(n).toLocaleString(undefined,{maximumFractionDigits:2});
async function api(path,data){const response=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(result.error);return result;}
async function action(fn){$('error').textContent='';try{await fn();await refresh();}catch(e){$('error').textContent=e.message;}}
function chosen(){return [...document.querySelectorAll('#examples input:checked')].map(x=>x.value);}
function updateCount(){$('example-count').textContent=`(${chosen().length} selected)`;}
function fields(){return {id:selectedId,name:$('name').value,kind:$('kind').value,model:$('model').value,endpoint:$('endpoint').value,context_tokens:Number($('context').value),memory:Number($('memory').value),days:[...$('days').selectedOptions].map(o=>o.value),mode:$('mode').value,start:Number($('start').value),end:Number($('end').value),seed:Number($('seed').value),example_ids:$('kind').value==='LLM'?chosen():[]};}
function load(p){selectedId=p?.id||'';$('profiles').value=selectedId;if(!p)return;for(const k of ['name','kind','model','endpoint','memory','seed','mode'])$(k).value=p[k];$('context').value=p.context_tokens;setWindow(p.start,p.end);for(const o of $('days').options)o.selected=p.days.includes(o.value);for(const c of document.querySelectorAll('#examples input'))c.checked=p.example_ids.includes(c.value);$('mode').onchange();$('kind').onchange();updateCount();}
function cell(tr,value){const td=document.createElement('td');td.textContent=value;tr.append(td);return td;}
function renderResults(rows){$('results').replaceChildren();for(const row of rows){const tr=document.createElement('tr'),m=row.metrics;for(const v of [row.dispatcher+(row.model?' / '+row.model:''),`${row.day} ${time(row.start)}–${time(row.end)}`,row.memory+' min',row.seed,fmt(m.total_wait),fmt(m.capacity_failure_pct),fmt(m.overall_utilization_pct),m.extra_buses,fmt(m.extra_bus_hours)])cell(tr,v);const td=cell(tr,'');const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent=`${row.example_count} examples`;details.append(summary);for(const e of row.examples){const p=document.createElement('p');p.textContent=`${e.case_id}: ${e.day} ${time(e.time)}, ${e.horizon_minutes} min, seed ${e.seed}`;details.append(p);}const pre=document.createElement('pre');pre.textContent=JSON.stringify(row.profile,null,2);details.append(pre);td.append(details);const replay=document.createElement('button');replay.textContent='Replay';replay.onclick=()=>action(async()=>{stopPreview();followLive=false;replayId=row.id;$('replay').max=row.frames-1;$('replay').value=0;await showFrame(0);});td.append(replay);const discard=document.createElement('button');discard.className='secondary';discard.textContent='Discard result';discard.onclick=()=>action(()=>api('lab/discard',{id:row.id,type:'result'}));td.append(discard);$('results').append(tr);}}
async function showFrame(index){if(!replayId)return;const id=replayId;const frame=await api('lab/frame',{id,index:Number(index)});if(id!==replayId)return;if(previewSelected){frame.map=workspaceMap;previewSelected=false;}$('viewer').contentWindow.postMessage({type:'nextbus-frame',frame},location.origin);$('frame-time').textContent=time(frame.time);$('replay').value=index;}
async function refresh(){if(polling)return;polling=true;try{const next=await api('lab');const profileValue=$('profiles').value;$('profiles').replaceChildren(new Option('New dispatcher',''));for(const p of next.profiles)$('profiles').add(new Option(p.name,p.id));$('profiles').value=profileValue;const selected=state?chosen():next.examples.slice(0,4).map(e=>e.case_id);$('examples').replaceChildren();for(const e of next.examples){const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.value=e.case_id;check.checked=selected.includes(e.case_id);check.onchange=updateCount;label.append(check,document.createTextNode(`${e.day} · ${time(e.time)} · ${e.horizon_minutes} min`));$('examples').append(label);}updateCount();if(JSON.stringify(state?.results)!==JSON.stringify(next.results))renderResults(next.results);state=next;const j=state.job;$('status').textContent=previewSelected?'Interactive preview':`${j.name||'Simulation workspace'} · ${j.status}`;$('progress').textContent=previewSelected?$('preview-status').textContent:j.error||`${j.day||''} ${j.time!=null?time(j.time):''}${j.status==='warming'?' · BASE0 checkpoint preparation':''}`;$('run').disabled=['running','warming'].includes(j.status);$('add-example').disabled=$('run').disabled;if(followLive&&j.id&&j.frames){replayId=j.id;$('replay').max=j.frames-1;const key=j.id+':'+j.frames;if(key!==lastFrame){lastFrame=key;await showFrame(j.frames-1);}}}finally{polling=false;}}
$('profiles').onchange=()=>load(state.profiles.find(p=>p.id===$('profiles').value));
$('mode').onchange=()=>{$('window').hidden=$('mode').value!=='window';};
$('kind').onchange=()=>{$('llm-settings').hidden=$('kind').value!=='LLM';};
$('save').onclick=()=>action(async()=>{const p=await api('lab/profile',fields());selectedId=p.id;await refresh();$('profiles').value=p.id;});
$('delete-profile').onclick=()=>action(async()=>{if(selectedId)await api('lab/discard',{type:'profile',id:selectedId});selectedId='';});
$('run').onclick=()=>action(async()=>{stopPreview();followLive=true;playing=false;lastFrame='';await api('lab/start',{...fields(),api_key:$('key').value});$('key').value='';});
$('all-examples').onclick=()=>{document.querySelectorAll('#examples input').forEach(c=>c.checked=true);updateCount();};
$('no-examples').onclick=()=>{document.querySelectorAll('#examples input').forEach(c=>c.checked=false);updateCount();};
$('add-example').onclick=()=>action(()=>api('lab/example',{day:$('example-day').value,start:minutes($('example-start').value),minutes:Number($('example-minutes').value)}));
$('live').onclick=()=>{stopPreview();followLive=true;playing=false;lastFrame='';action(refresh);};
$('replay').oninput=()=>{stopPreview();followLive=false;action(()=>showFrame($('replay').value));};
$('replay-play').onclick=()=>{stopPreview();followLive=false;playing=!playing;$('replay-play').textContent=playing?'Pause replay':'Play replay';if(playing)play();};
async function play(){if(!playing)return;const next=Number($('replay').value)+1;if(next>Number($('replay').max)){playing=false;$('replay-play').textContent='Play replay';return;}try{await showFrame(next);}catch(e){$('error').textContent=e.message;playing=false;}if(playing)setTimeout(play,500);}
$('viewer').onload=()=>{lastFrame='';};
(async()=>{try{const setup=await api('setup');initializeWindowTimes(setup.config.start,setup.config.end);workspaceMap={places:setup.places,routes:setup.routes,destinations:setup.destinations,facilities:setup.facilities};$('preview-config').value=JSON.stringify(setup.config,null,2);for(const day of setup.days){$('days').add(new Option(day,day,false,['2026-08-24','2026-08-26'].includes(day)));$('example-day').add(new Option(day,day,false,day==='2027-01-25'));}await refresh();setInterval(()=>refresh().catch(e=>$('error').textContent=e.message),2000);}catch(e){$('error').textContent=e.message;}})();

$('model').onchange=()=>{$('context').value=$('model').value==='gemini-3.5-flash-lite'?1048576:32768;};

let previewPlaying=false, previewBusy=false, previewEnd=0, previewTime=0, previewGeneration=0;
function stopPreview(){previewPlaying=false;previewGeneration++;$('preview-run').textContent='Run preview';}
function displayPreview(frame){
  previewTime=frame.time;previewSelected=true;
  $('viewer').contentWindow.postMessage({type:'nextbus-frame',frame},location.origin);
  $('frame-time').textContent=time(frame.time);
  $('preview-status').textContent=`Interactive preview · ${time(frame.time)}${frame.time>=previewEnd?' · Finished':''}`;
  $('preview-step').disabled=previewBusy||previewPlaying||frame.time>=previewEnd;
  $('preview-run').disabled=frame.time>=previewEnd;
}
$('preview-create').onclick=()=>action(async()=>{
  if(previewBusy)throw Error('Wait for the current preview step.');
  stopPreview();playing=false;$('replay-play').textContent='Play replay';followLive=false;replayId=null;
  const config=JSON.parse($('preview-config').value);
  const frame=await api('reset',config);previewEnd=config.end;
  displayPreview(frame);
});
async function stepPreview(){
  if(previewBusy||previewTime>=previewEnd)return;
  previewBusy=true;$('preview-create').disabled=true;$('preview-step').disabled=true;
  const generation=previewGeneration;
  try{const frame=await api('step',{minutes:5});if(generation===previewGeneration)displayPreview(frame);}
  finally{previewBusy=false;$('preview-create').disabled=false;$('preview-step').disabled=previewPlaying||previewTime>=previewEnd;}
}
$('preview-step').onclick=()=>action(stepPreview);
$('preview-run').onclick=()=>{
  if(previewPlaying){stopPreview();return;}
  previewPlaying=true;followLive=false;playing=false;replayId=null;
  $('preview-run').textContent='Pause preview';
  const generation=++previewGeneration;
  (async function loop(){
    if(!previewPlaying||generation!==previewGeneration)return;
    try{await stepPreview();}catch(e){$('error').textContent=e.message;stopPreview();}
    if(previewTime>=previewEnd)stopPreview();
    if(previewPlaying&&generation===previewGeneration)setTimeout(loop,100);
  })();
};

function timeOptionLabel(value){
  return String(Math.floor(value/60)).padStart(2,'0')+String(value%60).padStart(2,'0');
}
function initializeWindowTimes(first,last){
  serviceTimes=[];
  for(let t=Math.ceil(first/15)*15;t<=last;t+=15)serviceTimes.push(t);
  if(serviceTimes.length<2)throw Error('Service hours must include a 15-minute test window.');
  $('start').replaceChildren(...serviceTimes.slice(0,-1).map(t=>new Option(timeOptionLabel(t),String(t))));
  $('start').disabled=false;$('end').disabled=false;
  setWindow(420,570);
}
function setWindow(start,end){
  const available=serviceTimes.slice(0,-1);
  $('start').value=String(available.includes(start)?start:available[0]);
  updateWindowEnd(end);
}
function updateWindowEnd(preferred=Number($('end').value)){
  const choices=serviceTimes.filter(t=>t>Number($('start').value));
  $('end').replaceChildren(...choices.map(t=>new Option(timeOptionLabel(t),String(t))));
  $('end').value=String(choices.includes(preferred)?preferred:choices[0]);
}
$('start').onchange=()=>updateWindowEnd();
