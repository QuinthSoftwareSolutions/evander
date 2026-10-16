const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../evander_rabbitmq_addon/ui_static/resources.js'),'utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function app(){
  const nodes={};
  class Element{
    constructor(id=''){this.id=id;this.value='';this.children=[];this.dataset={};}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.children=[...items];if(this.id==='credential-connection')this.value=items[0]?.value||'';}
  }
  for(const id of ['directory','refresh','status','connections','profiles','leases','operations','save','settings','credential-save','credential-connection','credential-purpose','credential-username','credential-password'])nodes[id]=new Element(id);
  nodes.directory.value='root';nodes['credential-purpose'].value='read';
  const root=new Element();root.dataset.addon='sample-addon';
  const pending=[];
  const fetch=(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}));
  vm.runInNewContext(source,{document:{querySelector:()=>root,getElementById:id=>nodes[id],createElement:()=>new Element()},fetch,console});
  function deliver(index,value){pending[index].resolve({ok:true,json:async()=>value});}
  function batch(offset,connection='main',revision=1){
    deliver(offset,{revision,settings:{allocations_enabled:false,connections:{[connection]:{operations:[],credentials:{}}},profiles:{test:{options:{databases:['app']},idle_seconds:7200}}}});
    deliver(offset+1,{leases:[{lease_id:'rs-'+'1'.repeat(32),owner:{key:'owner'},profile_id:'test',state:'ready',revision:7,expires_at:9999999}]});
    deliver(offset+2,{operations:[]});
  }
  return {nodes,pending,deliver,batch};
}
test('directory changes discard stale responses and prevent actions on old rows',async()=>{
  const ui=app();ui.batch(0);await flush();
  const release=ui.nodes.leases.children[0].children[1].children.at(-1).children[0];
  ui.nodes.directory.value='root/other';ui.nodes.directory.oninput();
  await release.onclick();assert.equal(ui.pending.length,3);
  const pending=ui.nodes.refresh.onclick();
  ui.nodes.directory.value='root/final';ui.nodes.directory.oninput();
  ui.batch(3,'stale',2);await pending;
  assert.equal(ui.nodes.save.disabled,true);assert.equal(ui.nodes.connections.children.length,0);
  const current=ui.nodes.refresh.onclick();ui.batch(6,'current',3);await current;
  assert.ok(ui.nodes.settings.value.includes('current'));
  assert.ok(!ui.nodes.settings.value.includes('stale'));
});
test('credential form stores a reference in settings and clears its password immediately',async()=>{
  const ui=app();ui.batch(0,'main',3);await flush();
  ui.nodes['credential-username'].value='reader';ui.nodes['credential-password'].value='synthetic-password';
  const save=ui.nodes['credential-save'].onclick();
  assert.equal(ui.nodes['credential-password'].value,'');
  assert.equal(JSON.parse(ui.pending[3].options.body).values.password,'synthetic-password');
  ui.deliver(3,{credential_ref:'cred_'+'1'.repeat(32)});await flush();
  const settings=JSON.parse(ui.pending[4].options.body);
  assert.equal(settings.settings.connections.main.credentials.read.credential_ref,'cred_'+'1'.repeat(32));
  assert.ok(!ui.pending[4].options.body.includes('synthetic-password'));
  ui.deliver(4,{});await flush();ui.batch(5,'main',4);await save;
});
