// Common bounded representation for CDP, compatibility mode and helper snapshots.
export const roles = new Set(["button", "link", "textbox", "combobox", "checkbox", "radio", "tab", "menuitem", "option", "heading", "dialog", "alert", "gridcell", "row"]);
const roleOf = (node: any) => ({LayoutTableRow:"row",LayoutTableCell:"gridcell",cell:"gridcell",rowheader:"gridcell",columnheader:"gridcell"} as Record<string,string>)[node.role?.value] ?? node.role?.value;
const states = ["checked", "selected", "expanded", "disabled", "readonly", "focused", "editable", "required"];
export function accessibleElements(nodes: any[]) {
  const byId = new Map(nodes.map(node => [String(node.nodeId), node]));
  const elements: any[] = [];
  const signature = (node: any) => {
    const properties = Object.fromEntries((node.properties ?? []).filter((p: any) => states.includes(p.name)).map((p: any) => [p.name,p.value?.value]));
    return { role: roleOf(node), name: String(node.name?.value ?? "").slice(0,400),
      value: String(node.value?.value ?? "").slice(0,1000), ...properties };
  };
  for (const node of nodes) {
    if (node.ignored || !node.backendDOMNodeId || !roles.has(roleOf(node))) continue;
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
  return {elements,truncated:nodes.filter(n=>!n.ignored && n.backendDOMNodeId && roles.has(roleOf(n))).length>600};
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
  return JSON.stringify(elements.filter(item=>["textbox","combobox","checkbox","radio","heading","dialog","alert"].includes(item.role))
    .map(item=>({ref:item.ref,role:item.role,name:item.name,value:item.value,checked:item.checked,
      selected:item.selected,expanded:item.expanded,disabled:item.disabled,readonly:item.readonly})));
}
