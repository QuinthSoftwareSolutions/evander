// Host-owned interpreter. Agent input is JSON data, never executable JavaScript.
const reply = value => ({evander: 1, ...value});
const canonical = value => {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(
    key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
};
const fields = ['name', 'x', 'y', 'width', 'height', 'visible', 'opacity', 'rotation',
  'fills', 'strokes', 'strokeWeight', 'effects', 'cornerRadius', 'layoutMode',
  'itemSpacing', 'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight',
  'layoutSizingHorizontal', 'layoutSizingVertical', 'primaryAxisSizingMode',
  'counterAxisSizingMode', 'primaryAxisAlignItems', 'counterAxisAlignItems',
  'clipsContent', 'constraints', 'boundVariables', 'characters', 'fontName', 'fontSize',
  'textStyleId', 'fillStyleId', 'lineHeight', 'letterSpacing', 'textAlignHorizontal',
  'textAlignVertical', 'componentProperties'];
function serialize(node) {
  const result = {id: node.id, type: node.type, parent_id: node.parent ? node.parent.id : null};
  result.ancestors = [];
  for (let ancestor = node.parent; ancestor; ancestor = ancestor.parent) result.ancestors.push(ancestor.id);
  for (const field of fields) {
    if (!(field in node)) continue;
    const value = node[field];
    if (typeof value !== 'symbol' && value !== undefined) result[field] = JSON.parse(JSON.stringify(value));
  }
  if (node.type === 'TEXT' && node.characters.length) {
    result.textSegments = node.getStyledTextSegments(['fontName', 'fontSize', 'fills', 'textStyleId']);
  }
  if ('children' in node) result.children = node.children.map(child => child.id);
  return result;
}
function tree(root) {
  const result = [], pending = [root];
  while (pending.length) {
    const node = pending.pop();
    if (result.length >= 1000) throw new Error('Select a smaller design subtree');
    result.push(serialize(node));
    if ('children' in node) pending.push(...Array.from(node.children).reverse());
  }
  return result;
}
function inside(node, rootIds) {
  for (let current = node; current; current = current.parent) {
    if (rootIds.includes(current.id)) return true;
  }
  return false;
}
async function find(id) {
  const node = await figma.getNodeByIdAsync(id);
  if (!node || node.removed) throw new Error('Node no longer exists');
  return node;
}
async function fonts(node) {
  const texts = [];
  if (node.type === 'TEXT') texts.push(node);
  if ('findAllWithCriteria' in node) texts.push(...node.findAllWithCriteria({types: ['TEXT']}));
  for (const text of texts) {
    const names = text.characters.length ? text.getStyledTextSegments(['fontName']).map(s => s.fontName) : [text.fontName];
    for (const font of names) if (font && typeof font !== 'symbol') await figma.loadFontAsync(font);
  }
}
async function properties(node, props) {
  await fonts(node);
  const isText = node.type === 'TEXT';
  if (['characters', 'font_size', 'font_family', 'font_style', 'text_style_id'].some(
      key => props[key] != null) && !isText) throw new Error('Text properties require TEXT');
  if (isText && (props.font_family != null || props.font_style != null)) {
    const font = {family: props.font_family || 'Inter', style: props.font_style || 'Regular'};
    await figma.loadFontAsync(font);
    node.fontName = font;
  }
  if (props.text_style_id != null) await node.setTextStyleIdAsync(props.text_style_id);
  const mapping = {name:'name', x:'x', y:'y', characters:'characters', font_size:'fontSize',
    corner_radius:'cornerRadius', opacity:'opacity', visible:'visible', layout_mode:'layoutMode',
    item_spacing:'itemSpacing', horizontal_sizing:'layoutSizingHorizontal', vertical_sizing:'layoutSizingVertical'};
  for (const [inputKey, nodeKey] of Object.entries(mapping)) {
    if (props[inputKey] == null) continue;
    if (!(nodeKey in node)) throw new Error('Property is not supported on this node');
    node[nodeKey] = props[inputKey];
  }
  if (props.width != null || props.height != null) node.resize(props.width || node.width, props.height || node.height);
  if (props.padding != null) {
    if (!('paddingTop' in node)) throw new Error('Padding requires a layout frame');
    node.paddingTop = node.paddingBottom = node.paddingLeft = node.paddingRight = props.padding;
  }
  if (props.fill != null) {
    const hex = props.fill.slice(1);
    node.fills = [{type:'SOLID', color:{r:parseInt(hex.slice(0,2),16)/255,
      g:parseInt(hex.slice(2,4),16)/255, b:parseInt(hex.slice(4,6),16)/255}}];
  }
  if (props.fill_variable_id != null) {
    const variable = await figma.variables.getVariableByIdAsync(props.fill_variable_id);
    if (!variable || variable.resolvedType !== 'COLOR') throw new Error('Expected a color variable');
    const fill = node.fills.find(paint => paint.type === 'SOLID');
    if (!fill) throw new Error('A color variable needs a solid fill');
    node.fills = [figma.variables.setBoundVariableForPaint(fill, 'color', variable)];
  }
}

if (input.action === 'pages') return reply({pages:figma.root.children.map(p => ({id:p.id, name:p.name}))});
if (input.action === 'exists') return reply({exists:!!(await figma.getNodeByIdAsync(input.node_id))});
let root = input.node_id ? await find(input.node_id) : null;
if (root) {
  let page = root;
  while (page.parent && page.type !== 'PAGE') page = page.parent;
  if (page.type !== 'PAGE') throw new Error('Select a page or frame');
  await figma.setCurrentPageAsync(page);
}
if (input.action === 'inspect') {
  const nodes = tree(root), result = [];
  let bytes = 0, index = input.offset || 0;
  while (index < nodes.length) {
    const size = JSON.stringify(nodes[index]).length;
    if (size > 12000) throw new Error('Node data exceeds the read limit');
    if (bytes + size > 12000) break;
    result.push(nodes[index++]); bytes += size;
  }
  // A pagination consistency marker, not an authorization or revision digest.
  const serialized = canonical(nodes);
  let a = 2166136261, b = 5381;
  for (let i = 0; i < serialized.length; i++) {
    a = Math.imul(a ^ serialized.charCodeAt(i), 16777619);
    b = Math.imul(b, 33) ^ serialized.charCodeAt(i);
  }
  return reply({nodes:result, next_offset:index < nodes.length ? index : null,
    read_token:`${serialized.length}:${a >>> 0}:${b >>> 0}`});
}
const allowed = input.allowed_roots || [];
if (root && allowed.length && !inside(root, allowed)) throw new Error('Outside granted edit area');
if (root && canonical(tree(root)) !== canonical(input.expected)) throw new Error('Design changed');
if (input.action === 'cleanup_test') {
  if (root.type !== 'PAGE' || root.name !== input.expected_name || !root.name.startsWith('Evander connection test ')) {
    throw new Error('Not the temporary connection-test page');
  }
  const id = root.id;
  root.remove();
  return reply({removedNodeIds:[id]});
}
for (const [id, expected] of Object.entries(input.expected_sources || {})) {
  if (canonical(tree(await find(id))) !== canonical(expected)) throw new Error('Source proposal changed');
}
const created = [], mutated = new Set(), removed = [];
function requireWrite(node) {
  if (allowed.length && !inside(node, allowed)) throw new Error('Outside granted edit area');
  if (root && !inside(node, [root.id])) throw new Error('Outside inspected subtree');
}
if (input.action === 'create_area') {
  if (!root && allowed.length) throw new Error('Page creation requires whole-file edit permission');
  let area;
  if (root) {
    requireWrite(root);
    if (!('appendChild' in root)) throw new Error('Parent cannot hold children');
    area = figma.createFrame();
    root.appendChild(area);
    area.layoutMode = 'VERTICAL';
    area.resize(1440, 900);
    area.x = Math.max(0, ...root.children.filter(n => n.id !== area.id).map(n => n.x + n.width)) + 80;
    mutated.add(root.id);
  } else {
    area = figma.createPage();
  }
  area.name = input.name;
  created.push(area.id);
} else {
  // Resolve and authorize the complete batch before the first canvas mutation.
  const operations = [];
  for (const edit of input.edits || []) {
    const node = edit.node_id ? await find(edit.node_id) : null;
    const parent = edit.parent_id ? await find(edit.parent_id) : null;
    if (node && edit.action !== 'clone') requireWrite(node);
    if (node && edit.action === 'clone' && input.action !== 'compose') requireWrite(node);
    if (parent) requireWrite(parent);
    if (parent && !('appendChild' in parent)) throw new Error('Parent cannot hold children');
    if (node && ['remove','move'].includes(edit.action) && node.id === root.id) throw new Error('Cannot move/remove the inspected root');
    if (node && parent && inside(parent, [node.id])) throw new Error('Cannot parent a node below itself');
    const component = edit.component_id ? await find(edit.component_id) : null;
    if (component && component.type !== 'COMPONENT') throw new Error('Expected a component');
    operations.push({edit,node,parent,component});
  }
  for (const item of operations) {
    const {edit,parent,component} = item;
    let node = item.node;
    if (node) await fonts(node);
    if (edit.action === 'create') {
      const creators = {FRAME:()=>figma.createFrame(), TEXT:()=>figma.createText(),
        RECTANGLE:()=>figma.createRectangle(), ELLIPSE:()=>figma.createEllipse(),
        INSTANCE:()=>component.createInstance()};
      node = creators[edit.kind]();
      await fonts(node);
      parent.appendChild(node); created.push(node.id); mutated.add(parent.id);
    } else if (edit.action === 'clone') {
      node = node.clone(); parent.appendChild(node); created.push(node.id); mutated.add(parent.id);
    } else if (edit.action === 'move') {
      if (node.parent) mutated.add(node.parent.id);
      parent.appendChild(node); mutated.add(parent.id); mutated.add(node.id);
    } else if (edit.action === 'remove') {
      if (node.parent) mutated.add(node.parent.id);
      removed.push(node.id); node.remove(); continue;
    } else {
      mutated.add(node.id);
    }
    await properties(node, edit.properties || {});
  }
}
return reply({createdNodeIds:created, mutatedNodeIds:Array.from(mutated), removedNodeIds:removed});
