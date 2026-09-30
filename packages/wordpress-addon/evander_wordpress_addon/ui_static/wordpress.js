const $=id=>document.getElementById(id);
let revision=0;
$('directory').value=new URL(location.href).searchParams.get('directory')||'root';
const base='/api/addons/wordpress-addon';
const endpoint=path=>`${base}${path}${path.includes('?')?'&':'?'}directory=${encodeURIComponent($('directory').value)}`;
async function request(path,body,method='GET'){
 const response=await fetch(endpoint(path),{method,headers:body?{'content-type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
 const result=await response.json();if(!response.ok)throw new Error(result.error||'Request failed');return result;
}
function text(tag,value){const node=document.createElement(tag);node.textContent=value;return node;}
function action(label,run){const button=text('button',label);button.onclick=()=>busy(run);return button;}
function status(message){$('message').textContent=message;if($('report').open)$('report-message').textContent=message;}
async function busy(run){try{status('Working…');await run();status('Ready');}catch(error){status(error.message);}}
async function load(){
 const result=await request('/settings');revision=result.revision;$('settings').value=JSON.stringify(result.settings,null,2);$('sites').replaceChildren();
 for(const [id,site] of Object.entries(result.settings.sites)){
  const row=text('article','');row.append(text('strong',id+' · '+site.origin),text('p',site.enabled?'Enabled':'Disabled'));
  row.append(action('Discover',async()=>show(await request('/discover?site_id='+encodeURIComponent(id)))),action('Test recovery',async()=>{await request('/test',{site_id:id},'POST');await load();}));$('sites').append(row);
 }
 $('download').href=endpoint('/companion.zip');
 const history=await request('/history');$('history').replaceChildren();
 for(const change of history.changes){const row=text('article','');row.append(text('strong',change.summary),text('p',`${change.state} · ${new Date(change.updated_at*1000).toLocaleString()}`),action('View report',async()=>show(await request(`/changes/${change.change_id}?site_id=${encodeURIComponent(change.site_id)}`))));$('history').append(row);}
}
function show(value){
 $('receipt').replaceChildren(text('h2',value.summary||'Website capabilities'));
 $('report-message').textContent='';
 if(value.state)$('receipt').append(text('p',value.state));
 if(value.checks)$('receipt').append(text('p',value.checks.regressions?.length?'Verification found functional regressions.':(value.checks.first?.uncertain||value.checks.second?.uncertain?'Verification is incomplete.':'Checks found no new functional regressions. Review the screenshots for appearance.')));
 if(value.error)$('receipt').append(text('p',value.error));
 for(const url of value.urls||[]){const link=text('a',url);link.href=url;link.target='_blank';link.rel='noopener';$('receipt').append(link,text('br',''));}
 if(['published','prepared','conflict','uncertain','recovery_required'].includes(value.state))$('receipt').append(action('Restore this change',async()=>{
   await request(`/changes/${value.change_id}/restore`,{site_id:value.site_id,expected_revision:value.revision,idempotency_key:crypto.randomUUID()},'POST');$('report').close();await load();
 }));
 if(['conflict','uncertain','recovery_required'].includes(value.state))$('receipt').append(action('Verify and keep current website',async()=>{
   await request(`/changes/${value.change_id}/resolve`,{site_id:value.site_id,expected_revision:value.revision,idempotency_key:crypto.randomUUID()},'POST');$('report').close();await load();
 }));
 for(const shot of value.screenshots||[]){const figure=text('figure','');const image=document.createElement('img');image.alt=`${shot.phase} ${shot.path} at ${shot.viewport.width}px`;image.loading='lazy';image.src=endpoint(`/changes/${value.change_id}/screenshots/${shot.artifact}`);figure.append(text('figcaption',image.alt),image);$('receipt').append(figure);}
 if(value.change_id){const details=text('details','');details.append(text('summary','Change details'),text('pre',JSON.stringify(value,null,2)));$('receipt').append(details);}else $('receipt').append(text('pre',JSON.stringify(value,null,2)));
 if(!$('report').open)$('report').showModal();
}
$('close').onclick=()=>$('report').close();$('reload').onclick=()=>busy(load);
$('save').onclick=()=>busy(async()=>{await request('/settings',{expected_revision:revision,settings:JSON.parse($('settings').value)},'PUT');await load();});
busy(load);
