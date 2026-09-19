// A small Plugin API host. Tests execute the production interpreter unchanged.
import fs from 'node:fs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const state = input.state || {next:10,nodes:{
  '0:0':{id:'0:0',type:'DOCUMENT',name:'Document',parent_id:null,children:['0:1']},
  '0:1':{id:'0:1',type:'PAGE',name:'Existing designs',parent_id:'0:0',children:[]}
}};
const loaded = new Set();
const fontKey = f => JSON.stringify(f);
let currentPage='0:1', switches=0;
function wrap(id) {if(!state.nodes[id]||state.nodes[id].removed)return null;return new Node(id);}
function fontCheck(node) {if(node.type==='TEXT'&&!loaded.has(fontKey(node.fontName)))throw new Error('Unloaded font');}
class Node {
  constructor(id){this.key=id;for(const key of Object.keys(state.nodes[id])){
    if(['parent_id','children','removed'].includes(key))continue;
    Object.defineProperty(this,key,{enumerable:true,get:()=>state.nodes[id][key],set:value=>{
      if(key==='fontName'&&!loaded.has(fontKey(value)))throw new Error('Unloaded new font');
      if(key==='characters'||key==='fontSize')fontCheck(this);state.nodes[id][key]=value;}});
  }}
  get parent(){return wrap(state.nodes[this.key].parent_id);}
  get children(){return (state.nodes[this.key].children||[]).map(wrap).filter(Boolean);}
  get removed(){return state.nodes[this.key].removed||false;}
  appendChild(child){fontCheck(child);const old=state.nodes[child.id].parent_id;
    if(old)state.nodes[old].children=state.nodes[old].children.filter(id=>id!==child.id);
    state.nodes[child.id].parent_id=this.id;state.nodes[this.id].children.push(child.id);}
  resize(width,height){fontCheck(this);this.width=width;this.height=height;}
  getStyledTextSegments(){return [{start:0,end:this.characters.length,characters:this.characters,fontName:this.fontName,
    fontSize:this.fontSize,fills:this.fills,textStyleId:this.textStyleId}];}
  findAllWithCriteria({types}){return this.children.flatMap(n=>[...(types.includes(n.type)?[n]:[]),...n.findAllWithCriteria({types})]);}
  remove(){if(this.parent)state.nodes[this.parent.id].children=state.nodes[this.parent.id].children.filter(id=>id!==this.id);
    for(const child of this.children)child.remove();state.nodes[this.id].removed=true;}
  clone(){const clone=create(this.type);const children=this.children;
    state.nodes[clone.id]={...structuredClone(state.nodes[this.id]),id:clone.id,parent_id:currentPage,children:[]};
    for(const child of children)clone.appendChild(child.clone());return wrap(clone.id);}
  createInstance(){if(this.type!=='COMPONENT')throw new Error('Not component');const n=this.clone();state.nodes[n.id].type='INSTANCE';return wrap(n.id);}
  async setTextStyleIdAsync(id){
    const style=state.textStyles?.[id];if(!style)throw new Error('Unknown text style');
    // Applying a style changes its font without loading it for later edits.
    Object.assign(state.nodes[this.id],structuredClone(style),{textStyleId:id});
  }
}
function create(type){const id=`1:${state.next++}`;
  state.nodes[id]={id,type,name:type,parent_id:type==='PAGE'?'0:0':currentPage,children:[],
    x:0,y:0,width:100,height:100,visible:true,opacity:1,fills:[],strokes:[],effects:[],
    cornerRadius:0,layoutMode:'NONE',itemSpacing:0,paddingTop:0,paddingBottom:0,paddingLeft:0,paddingRight:0,
    layoutSizingHorizontal:'FIXED',layoutSizingVertical:'FIXED'};
  if(type==='TEXT')Object.assign(state.nodes[id],{characters:'',fontName:{family:'Inter',style:'Regular'},fontSize:12,textStyleId:''});
  state.nodes[state.nodes[id].parent_id].children.push(id);return wrap(id);}
const figma={root:wrap('0:0'),get currentPage(){return wrap(currentPage);},
  set currentPage(_){throw new Error('Use setCurrentPageAsync');},
  async setCurrentPageAsync(page){if(++switches>1)throw new Error('Multiple page switches');currentPage=page.id;},
  async getNodeByIdAsync(id){return wrap(id);},async loadFontAsync(font){loaded.add(fontKey(font));},
  createPage:()=>create('PAGE'),createFrame:()=>create('FRAME'),createText:()=>create('TEXT'),
  createRectangle:()=>create('RECTANGLE'),createEllipse:()=>create('ELLIPSE'),
  variables:{async getVariableByIdAsync(id){return {id,resolvedType:'COLOR'};},
    setBoundVariableForPaint(paint,key,variable){return {...paint,boundVariables:{[key]:{id:variable.id}}};}}
};
try {const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
  const result=await new AsyncFunction('figma',input.code)(figma);
  process.stdout.write(JSON.stringify({state,result}));
}catch(error){process.stdout.write(JSON.stringify({state,error:error.message}));}
