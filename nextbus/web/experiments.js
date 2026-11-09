const $ = id => document.getElementById(id);
let state, selectedId='', experimentId='', replayId=null, followLive=true, lastFrame='', playing=false, polling=false;
let workspaceMap, previewSelected=false, serviceTimes=[];
const time=n=>`${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
const minutes=s=>{const [h,m]=s.split(':').map(Number);return h*60+m;};
const fmt=n=>n==null?'—':Number(n).toLocaleString(undefined,{maximumFractionDigits:2});
async function api(path,data){
  const response=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await response.json();if(!response.ok)throw Error(result.error);return result;
}
async function action(fn,errorId='error'){
  $(errorId).textContent='';try{await fn();await refresh();}catch(e){$(errorId).textContent=e.message;}
}
function chosen(){return [...document.querySelectorAll('#examples input:checked')].map(x=>x.value);}
function updateCount(){$('example-count').textContent=`(${chosen().length} selected)`;}
function dispatcherFields(){
  return {id:selectedId,name:$('name').value,kind:$('kind').value,model:$('model').value,
    endpoint:$('endpoint').value,context_tokens:Number($('context').value),memory:Number($('memory').value),
    example_ids:$('kind').value==='LLM'?chosen():[],crowd_level:$('crowd-level').value,
    rule_share:Number($('rule-share').value)/100,minimum_buses:Number($('minimum-buses').value)};
}
function experimentFields(){
  return {id:experimentId,name:$('experiment-name').value,dispatcher_id:$('experiment-dispatcher').value,
    days:[...$('days').selectedOptions].map(o=>o.value),mode:$('mode').value,
    start:Number($('start').value),end:Number($('end').value),seed:Number($('seed').value)};
}
function options(id,items,placeholder){
  const select=$(id),selected=select.value;
  select.replaceChildren();if(placeholder)select.add(new Option(placeholder,''));
  items.forEach(item=>select.add(new Option(item.name,item.id)));
  if([...select.options].some(o=>o.value===selected))select.value=selected;
  else if(id==='experiment-dispatcher'&&selected){select.add(new Option('Discarded dispatcher — choose another',selected));select.value=selected;}
}
function loadDispatcher(p){
  selectedId=p?.id||'';$('profiles').value=selectedId;
  $('name').value=p?.name||'My dispatcher';$('kind').value=p?.kind||'LLM';
  $('model').value=p?.model||state.default_model;$('endpoint').value=p?.endpoint||state.default_endpoint;
  $('memory').value=p?.memory??30;$('context').value=p?.context_tokens||1048576;
  $('crowd-level').value=p?.crowd_level||'HIGH';$('rule-share').value=(p?.rule_share??.7)*100;
  $('minimum-buses').value=p?.minimum_buses??2;
  for(const c of document.querySelectorAll('#examples input'))c.checked=(p?.example_ids||[]).includes(c.value);
  const protectedBase=p?.id==='preset-base0';
  $('kind').closest('label').hidden=protectedBase;
  $('name').disabled=protectedBase;$('kind').disabled=protectedBase;$('save').disabled=protectedBase;
  $('delete-profile').disabled=!p||protectedBase;
  $('save').textContent=p?.baseline?'Save as custom dispatcher':'Save dispatcher';
  $('baseline-note').hidden=!protectedBase&&!p?.baseline;
  $('baseline-note').textContent=protectedBase?'BASE0 keeps scheduled service. It cannot be edited or discarded.':
    'Original BASE1 uses HIGH, 70%, and at least two crowded runs. Saving edits creates a custom dispatcher using the minimum total observed buses.';
  $('kind').onchange();updateCount();
}
function loadExperiment(e){
  experimentId=e?.id||'';$('experiments').value=experimentId;$('experiment-name').value=e?.name||'My experiment';
  const dispatcherId=e?.dispatcher_id||'preset-base0';
  if(![...$('experiment-dispatcher').options].some(o=>o.value===dispatcherId))$('experiment-dispatcher').add(new Option('Discarded dispatcher — choose another',dispatcherId));
  $('experiment-dispatcher').value=dispatcherId;
  $('seed').value=e?.seed??101;$('mode').value=e?.mode||'day';setWindow(e?.start??420,e?.end??570);
  for(const o of $('days').options)o.selected=(e?.days||['2026-08-24','2026-08-26']).includes(o.value);
  $('delete-experiment').disabled=!e;$('mode').onchange();syncKey();renderRunHistory();
}
function syncKey(){
  const dispatcher=state?.profiles.find(p=>p.id===$('experiment-dispatcher').value);
  $('key').closest('label').hidden=dispatcher?.kind!=='LLM';
}
function cell(tr,value){const td=document.createElement('td');td.textContent=value;tr.append(td);return td;}
function renderResults(rows){
  $('results').replaceChildren();
  for(const row of rows){
    const tr=document.createElement('tr'),m=row.metrics;
    for(const value of [row.experiment_name||row.dispatcher,row.dispatcher+(row.model?' / '+row.model:''),
      `${row.day} ${time(row.start)}–${time(row.end)}`,row.memory+' min',row.seed,fmt(m.total_wait),
      fmt(m.capacity_failure_pct),fmt(m.overall_utilization_pct),m.extra_buses,fmt(m.extra_bus_hours)])cell(tr,value);
    const td=cell(tr,''),details=document.createElement('details'),summary=document.createElement('summary');
    summary.textContent=`${row.example_count} examples`;details.append(summary);
    for(const e of row.examples){const p=document.createElement('p');p.textContent=`${e.case_id}: ${e.day} ${time(e.time)}, ${e.horizon_minutes} min, seed ${e.seed}`;details.append(p);}
    const pre=document.createElement('pre');pre.textContent=JSON.stringify(row.profile,null,2);details.append(pre);td.append(details);
    const discard=document.createElement('button');discard.className='secondary';discard.textContent='Discard result';
    discard.onclick=()=>action(()=>api('lab/discard',{id:row.id,type:'result'}),'experiment-error');td.append(discard);$('results').append(tr);
  }
}
function renderRunHistory(){
  const rows=(state?.results||[]).filter(r=>!experimentId||r.experiment_id===experimentId);
  options('run-history',rows.map(r=>({id:r.id,name:`${r.experiment_name||r.dispatcher} · ${r.day} ${time(r.start)}–${time(r.end)}`})),'Select a completed run');
}
function renderExamples(next){
  const selected=state?chosen():[];
  $('examples').replaceChildren();
  for(const e of next.examples){
    const label=document.createElement('label'),check=document.createElement('input');
    check.type='checkbox';check.value=e.case_id;check.checked=selected.includes(e.case_id);check.onchange=updateCount;
    label.append(check,document.createTextNode(`${e.day} · ${time(e.time)} · ${e.horizon_minutes} min`));
    const row=document.createElement('div');row.className='example-row';
    const discard=document.createElement('button');discard.type='button';discard.className='secondary example-discard';discard.textContent='Discard';
    discard.setAttribute('aria-label',`Discard example ${e.case_id} from ${e.day}`);
    discard.disabled=['running','warming'].includes(next.job.status);
    discard.onclick=()=>action(async()=>{await api('lab/discard',{type:'example',id:e.case_id});check.checked=false;updateCount();});
    row.append(label,discard);$('examples').append(row);
  }updateCount();
}
async function showFrame(index){
  if(!replayId)return;
  const id=replayId,frame=await api('lab/frame',{id,index:Number(index)});
  if(id!==replayId)return;
  if(previewSelected){frame.map=workspaceMap;previewSelected=false;}
  $('viewer').contentWindow.postMessage({type:'nextbus-frame',frame},location.origin);
  $('frame-time').textContent=time(frame.time);$('replay').value=index;
}
async function refresh(){
  if(polling)return;polling=true;
  try{
    const next=await api('lab');
    if(JSON.stringify(state?.profiles)!==JSON.stringify(next.profiles)){
      options('profiles',next.profiles,'New dispatcher');options('experiment-dispatcher',next.profiles);
    }
    if(JSON.stringify(state?.experiments)!==JSON.stringify(next.experiments))options('experiments',next.experiments,'New experiment');
    renderExamples(next);
    const rowsChanged=JSON.stringify(state?.results)!==JSON.stringify(next.results);
    state=next;if(rowsChanged){renderResults(next.results);renderRunHistory();}syncKey();
    const j=state.job;
    $('status').textContent=previewSelected?'Interactive preview':`${j.name||'Experiment'} · ${j.status}`;
    $('progress').textContent=previewSelected?$('preview-status').textContent:j.error||`${j.day||''} ${j.time!=null?time(j.time):''}${j.status==='warming'?' · Preparing BASE0 checkpoint':''}`;
    $('run').disabled=['running','warming'].includes(j.status);$('add-example').disabled=$('run').disabled;
    if(followLive&&j.id&&j.frames){
      replayId=j.id;$('replay').max=j.frames-1;
      const key=j.id+':'+j.frames;if(key!==lastFrame){lastFrame=key;await showFrame(j.frames-1);}
    }
  }finally{polling=false;}
}
$('profiles').onchange=()=>loadDispatcher(state.profiles.find(p=>p.id===$('profiles').value));
$('experiments').onchange=()=>loadExperiment(state.experiments.find(e=>e.id===$('experiments').value));
$('experiment-dispatcher').onchange=syncKey;
$('mode').onchange=()=>{$('window').hidden=$('mode').value!=='window';};
$('kind').onchange=()=>{$('llm-settings').hidden=$('kind').value!=='LLM';$('rule-settings').hidden=$('kind').value!=='RULE';};
$('save').onclick=()=>action(async()=>{const p=await api('lab/profile',dispatcherFields());await refresh();loadDispatcher(p);});
$('delete-profile').onclick=()=>action(async()=>{if(selectedId)await api('lab/discard',{type:'profile',id:selectedId});await refresh();loadDispatcher();});
async function saveExperiment(){const e=await api('lab/experiment',experimentFields());await refresh();loadExperiment(e);return e;}
$('save-experiment').onclick=()=>action(saveExperiment,'experiment-error');
$('delete-experiment').onclick=()=>action(async()=>{if(experimentId)await api('lab/discard',{type:'experiment',id:experimentId});await refresh();loadExperiment();},'experiment-error');
$('run').onclick=()=>action(async()=>{
  const e=await saveExperiment();stopPreview();followLive=true;playing=false;lastFrame='';
  await api('lab/start',{experiment_id:e.id,api_key:$('key').value});$('key').value='';
},'experiment-error');
$('all-examples').onclick=()=>{document.querySelectorAll('#examples input').forEach(c=>c.checked=true);updateCount();};
$('no-examples').onclick=()=>{document.querySelectorAll('#examples input').forEach(c=>c.checked=false);updateCount();};
$('add-example').onclick=()=>action(()=>api('lab/example',{day:$('example-day').value,start:minutes($('example-start').value),minutes:Number($('example-minutes').value)}));
$('run-history').onchange=()=>action(async()=>{
  const row=state.results.find(r=>r.id===$('run-history').value);if(!row)return;
  stopPreview();playing=false;followLive=false;replayId=row.id;$('replay').max=row.frames-1;$('replay').value=0;await showFrame(0);
},'experiment-error');
$('live').onclick=()=>{stopPreview();followLive=true;playing=false;lastFrame='';action(refresh,'experiment-error');};
$('replay').oninput=()=>{stopPreview();followLive=false;action(()=>showFrame($('replay').value),'experiment-error');};
$('replay-play').onclick=()=>{
  if(!replayId){$('experiment-error').textContent='Select a recorded run or start an experiment first.';return;}
  stopPreview();followLive=false;playing=!playing;$('replay-play').textContent=playing?'Pause replay':'Play replay';if(playing)play();
};
async function play(){
  if(!playing)return;const next=Number($('replay').value)+1;
  if(next>Number($('replay').max)){playing=false;$('replay-play').textContent='Play replay';return;}
  try{await showFrame(next);}catch(e){$('experiment-error').textContent=e.message;playing=false;}
  if(playing)setTimeout(play,500);
}
$('viewer').onload=()=>{lastFrame='';};
$('model').onchange=()=>{$('context').value=$('model').value==='gemini-3.5-flash-lite'?1048576:32768;};
(async()=>{
  try{
    const setup=await api('setup');initializeWindowTimes(setup.config.start,setup.config.end);
    workspaceMap={places:setup.places,routes:setup.routes,destinations:setup.destinations,facilities:setup.facilities};
    $('preview-config').value=JSON.stringify(setup.config,null,2);
    for(const day of setup.days){$('days').add(new Option(day,day,false,['2026-08-24','2026-08-26'].includes(day)));$('example-day').add(new Option(day,day,false,day==='2027-01-25'));}
    await refresh();loadDispatcher();loadExperiment();
    setInterval(()=>refresh().catch(e=>$('experiment-error').textContent=e.message),2000);
  }catch(e){$('error').textContent=e.message;}
})();
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
$('map-only').onclick=()=>{
  const hidden=document.body.classList.toggle('panels-hidden');
  $('map-only').textContent=hidden?'Show panels':'Map only';
  $('map-only').setAttribute('aria-pressed',String(hidden));
  $('viewer').contentWindow.postMessage({type:'nextbus-panels',hidden},location.origin);
};
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==$('viewer').contentWindow)return;
  if(event.data?.type==='nextbus-map-ready'){
    $('viewer').contentWindow.postMessage({type:'nextbus-panels',hidden:document.body.classList.contains('panels-hidden')},location.origin);
  }
});
