// Common bounded representation for CDP, compatibility mode and helper snapshots.
import {nativeControls, dateValue, optionLabel} from "./native-controls.js";
export const roles = new Set(["button", "link", "textbox", "combobox", "date", "spinbutton", "checkbox", "radio", "tab", "menuitem", "option", "heading", "dialog", "alert", "gridcell", "row"]);
const roleOf = (node: any) => ({Date:"date",LayoutTableRow:"row",LayoutTableCell:"gridcell",cell:"gridcell",rowheader:"gridcell",columnheader:"gridcell"} as Record<string,string>)[node.role?.value] ?? node.role?.value;
const states = ["checked", "selected", "expanded", "disabled", "readonly", "focused", "editable", "required"];
export function accessibleElements(nodes: any[]) {
  const byId = new Map([...nodes,...nodes.flatMap(node=>node.evanderChildren ?? [])].map(node => [String(node.nodeId), node]));
  const native = nativeControls(nodes);
  const owned = (node: any) => {
    const seen=new Set(); let parent=node;
    while (parent?.parentId && !seen.has(parent.parentId)) {
      seen.add(parent.parentId); parent=byId.get(String(parent.parentId));
      if (parent && native.has(parent.backendDOMNodeId)) return true;
    }
    return false;
  };
  const elements: any[] = [];
  const signature = (node: any) => {
    const properties = Object.fromEntries((node.properties ?? []).filter((p: any) => states.includes(p.name)).map((p: any) => [p.name,p.value?.value]));
    const control=native.get(node.backendDOMNodeId);
    const value=properties.editable && roleOf(node)==="spinbutton" ?
      (node.properties?.find((p: any)=>p.name==="valuetext")?.value?.value ?? node.value?.value) : node.value?.value;
    const extra=control?.kind==="date" ? {native_control:"date",native_input_verified:control.verified,
      ...(control.verified?{readonly:control.readonly,min:control.min,max:control.max,step:control.step,step_base:control.stepBase}:{}),value_format:"YYYY-MM-DD",
      date_parts:Object.fromEntries(control.parts.map(p=>[p.kind,p.value]))} :
      control?.kind==="select" ? {native_control:"select",options:control.options.map(o=>({name:o.name,disabled:o.disabled}))} : {};
    return { role: roleOf(node), name: String(node.name?.value ?? "").slice(0,400),
      value: control?.kind==="date" ? dateValue(control.parts) : control?.kind==="select" ? optionLabel(value) : String(value ?? "").slice(0,1000), ...properties,
      ...(node.properties?.some((p: any)=>p.name==="settable" && p.value?.value===false) ? {readonly:true} : {}),...extra };
  };
  for (const node of nodes) {
    if (node.ignored || !node.backendDOMNodeId || !roles.has(roleOf(node)) || owned(node)) continue;
    const context: any[] = node.evanderContext ?? [];
    let parent = node, depth = 0;
    while (!node.evanderContext && parent && depth++ < 16) {
      parent = byId.get(String(parent.parentId));
      if (parent && ["row", "dialog", "form", "group", "region"].includes(roleOf(parent))) {
        // Row and form text disambiguate identically labelled controls.
        const children = (parent.childIds ?? []).slice(0,24).map((id: any) => byId.get(String(id)))
          .filter(Boolean).map((child: any) => String(child.name?.value ?? "")).filter(Boolean).join(" ").slice(0,600);
        context.push({role:roleOf(parent),name:String(parent.name?.value ?? "").slice(0,200),text:children});
      }
      if (context.length===3) break;
    }
    const data = {...signature(node),context};
    // Fingerprints stay endpoint-owned. The PA/classifier never supplies this value.
    elements.push({ref:"node-"+node.backendDOMNodeId,...data});
    if (elements.length===600) break;
  }
  return {elements,truncated:nodes.filter(n=>!n.ignored && n.backendDOMNodeId && roles.has(roleOf(n)) && !owned(n)).length>600};
}
export function targetSignature(value: any) {
  if (!value) return "";
  // Focus changes intentionally after focusing; preserve identity, values and semantic context.
  const {ref, focused, ...semantic} = value;
  return JSON.stringify(semantic);
}

export function pageSignature(elements: any[]) {
  // Protect the form selection and page identity as well as the chosen control.
  // Ignore focus and incidental static text (clocks, counters, animations).
  return JSON.stringify(elements.filter(item=>["textbox","combobox","date","spinbutton","checkbox","radio","heading","dialog","alert"].includes(item.role))
    .map(item=>({ref:item.ref,role:item.role,name:item.name,value:item.value,checked:item.checked,
      selected:item.selected,expanded:item.expanded,disabled:item.disabled,readonly:item.readonly,date_parts:item.date_parts})));
}
