const $ = selector => document.querySelector(selector);
let current = null;
let loadedDirectory = null;
const directory = () => loadedDirectory || $('#directory').value.trim();
$('#directory').value = new URLSearchParams(location.search).get('directory') || 'root';
const status = (text, error=false) => { $('#status').textContent=text; $('#status').classList.toggle('error',error); };
const endpoint = (path, params={}) => `/api/addons/figma-addon${path}?${new URLSearchParams({directory:directory(),...params})}`;
async function api(path, method='GET', body=null, params={}) {
  if(method!=='GET' && $('#directory').value.trim()!==loadedDirectory)throw new Error('Open the selected directory before making changes.');
  const response=await fetch(endpoint(path,params),{method,credentials:'same-origin',
    headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
  const result=await response.json();
  if(!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
function element(tag,text,className) {const e=document.createElement(tag);if(text!=null)e.textContent=text;if(className)e.className=className;return e;}
function button(text,fn) {const b=element('button',text,'secondary');b.type='button';b.addEventListener('click',()=>run(b,fn));return b;}
async function run(control,fn) {control.disabled=true;status('Working…');try{await fn();status('Ready.');}catch(e){status(e.message,true);}finally{control.disabled=false;}}
function form(id,fn) {$(id).addEventListener('submit',event=>{event.preventDefault();const data=Object.fromEntries(new FormData(event.target));run(event.submitter,()=>fn(data,event.target));});}
async function load() {
  current=await api('/settings');render();
  $('[name=redirect_uri]').value=`${location.origin}/api/addons/figma-addon/oauth/callback`;
}
async function save(settings) {await api('/settings','PUT',{expected_revision:current.revision,settings});await load();}
function options(select,records) {
  const previous=select.value;select.replaceChildren();
  for(const [id,item] of Object.entries(records)){const option=element('option',item.name||id);option.value=id;select.append(option);}
  if(records[previous])select.value=previous;
}
function render() {
  const connections=$('#connections');connections.replaceChildren();
  for(const [id,config] of Object.entries(current.settings.connections)) {
    const state=current.connections[id],card=element('div',null,'card'),details=element('div');
    details.append(element('strong',id),element('div',state.authoring_ready?'Connected · unattended authoring verified':state.connected?'Connected · authoring test required':'Not connected','badge'));
    card.append(details,button('Edit settings',()=>{const form=$('#connection-form');form.closest('details').open=true;form.elements.id.value=id;for(const [key,value] of Object.entries(config))if(form.elements[key])form.elements[key].value=value||'';}),
      button('Connect',async()=>{const result=await api('/oauth/start','POST',{connection_id:id});location.assign(result.authorize_url);}),
      button('Disconnect',async()=>{await api('/disconnect','POST',{connection_id:id});await load();}));
    connections.append(card);
  }
  if(!connections.children.length)connections.append(element('p','Add a connection to get started.'));
  options($('#connection-select'),current.settings.connections);
  const targets=$('#targets');targets.replaceChildren();
  for(const [id,target] of Object.entries(current.settings.targets)) {
    const card=element('div',null,'card'),details=element('div'),link=element('a',target.name);
    link.href=`https://www.figma.com/design/${target.file_key}`;link.target='_blank';link.rel='noopener noreferrer';
    details.append(link,element('div',`${id} · ${target.connection_id}`,'badge'));
    for(const [session,grant] of Object.entries(target.grants))details.append(element('div',`${session}: ${grant.access.join(', ')||'no access'}${grant.edit_roots.length?' · roots '+grant.edit_roots.join(', '):''}`,'badge'));
    const testDetails=element('details'),summary=element('summary','Test unattended authoring');
    testDetails.append(summary,element('p','Creates a temporary test page in this file, edits and renders native content, then removes the page.'),
      button('Run test in this file',async()=>{const result=await api('/test','POST',{target_id:id});await load();if(result.status!=='passed')throw new Error(result.error||'Authoring test failed. Inspect operation history.');}));
    card.append(details,testDetails);targets.append(card);
  }
  document.querySelectorAll('.target-select').forEach(select=>options(select,current.settings.targets));
}
form('#directory-form',async()=>{
  const previous=loadedDirectory;loadedDirectory=$('#directory').value.trim();
  try{await load();}catch(error){loadedDirectory=previous;$('#directory').value=previous;throw error;}
  history.replaceState(null,'',`?${new URLSearchParams({directory:directory()})}`);$('#operations').replaceChildren();$('#snapshots').replaceChildren();
});
form('#connection-form',async data=>{
  const settings=structuredClone(current.settings),{id,...config}=data;
  config.rest_token_env ||= null;settings.connections[id]=config;await save(settings);
});
form('#target-form',async data=>{
  const url=new URL(data.file_url),parts=url.pathname.split('/');
  if(url.protocol!=='https:'||url.hostname!=='www.figma.com'||parts[1]!=='design'||!parts[2])throw new Error('Enter a Figma Design file URL.');
  const settings=structuredClone(current.settings),old=settings.targets[data.id];
  settings.targets[data.id]={name:data.name,connection_id:data.connection_id,file_key:parts[2],grants:old?.grants||{}};await save(settings);
});
form('#grant-form',async data=>{
  const settings=structuredClone(current.settings);
  settings.targets[data.target_id].grants[data.session_id]={access:['read','comment','edit'].filter(k=>data[k]==='on'),edit_roots:data.roots.split(',').map(s=>s.trim()).filter(Boolean)};
  await save(settings);
});
async function loadHistory() {
  const target_id=$('#history-target').value;if(!target_id)return;
  const data=await api('/history','GET',null,{target_id});
  const snapshots=$('#snapshots');snapshots.replaceChildren();
  for(const snapshot of data.snapshots){const card=element('article',null,'snapshot'),img=element('img');img.src=endpoint(`/snapshots/${snapshot.snapshot_id}/preview`,{target_id});img.alt=`Design snapshot captured ${snapshot.captured_at}`;
    const link=element('a','Open design');link.href=snapshot.file_url;link.target='_blank';link.rel='noopener noreferrer';card.append(img,element('p',snapshot.captured_at),link,element('div',snapshot.snapshot_id,'badge'));snapshots.append(card);}
  const operations=$('#operations');operations.replaceChildren();
  for(const operation of data.operations){const card=element('article',null,'card'),detail=element('div');detail.append(element('strong',`${operation.request.operation} · ${operation.status}`),element('div',`${operation.actor} · ${operation.created_at}`,'badge'));
    if(['running','uncertain'].includes(operation.status)){detail.append(button('Inspect current state',async()=>{
      const result=await api('/reconcile','POST',{target_id,operation_id:operation.operation_id});
      detail.append(element('pre',JSON.stringify(result.reconciliation||result,null,2)));
      if(result.observation_digest)detail.append(button('Accept current state and close operation',async()=>{
        await api('/resolve','POST',{target_id,operation_id:operation.operation_id,observation_digest:result.observation_digest});await loadHistory();}));
    }));}card.append(detail);operations.append(card);}
}
form('#history-form',loadHistory);
loadedDirectory=$('#directory').value.trim();
load().then(()=>status('Ready.')).catch(error=>status(error.message,true));
