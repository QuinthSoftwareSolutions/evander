/** Bounded native-control structure from Chrome AX, never page-authored scripts. */
export type DatePart = { kind: "year" | "month" | "day"; backend: number; value: number; min: number; max: number };
export type NativeControl = { kind: "date"; parts: DatePart[]; verified: boolean; readonly: boolean; min?: string; max?: string; step?: string; stepBase?: string } |
  { kind: "select"; options: { name: string; disabled: boolean; backend: number }[] };
export const optionLabel = (value: unknown) => String(value ?? "").replace(/\s+/g," ").trim();

export function nativeControls(nodes: any[]): Map<number, NativeControl> {
  const all = [...nodes, ...nodes.flatMap(node => node.evanderChildren ?? [])];
  const byId = new Map(all.filter(n=>n.nodeId).map(n=>[String(n.nodeId),n]));
  const result = new Map<number, NativeControl>();
  for (const node of nodes) {
    if (node.evanderChildrenTruncated || !node.backendDOMNodeId || !["Date","combobox"].includes(node.role?.value)) continue;
    const descendants: any[] = [], seen = new Set<string>(), pending = [...(node.childIds ?? [])].reverse();
    while (pending.length && seen.size < 2048) {
      const id = String(pending.pop());
      if (seen.has(id)) continue;
      seen.add(id); const child = byId.get(id);
      if (child) { descendants.push(child); pending.push(...[...(child.childIds ?? [])].reverse()); }
    }
    if (pending.length) continue;
    const property = (n: any, key: string) => n.properties?.find((p: any)=>p.name===key)?.value?.value;
    if (node.role.value === "Date") {
      const parts: DatePart[] = [];
      for (const child of descendants.filter(c=>c.role?.value==="spinbutton")) {
        const min = property(child,"valuemin"), max = property(child,"valuemax");
        const kind = max===12 ? "month" : max===31 ? "day" : max>31 ? "year" : null;
        const value = Number(child.value?.value ?? 0), backend = child.backendDOMNodeId;
        if (kind && Number.isSafeInteger(backend) && backend>0 && Number.isInteger(value) &&
            Number.isFinite(min) && Number.isFinite(max)) parts.push({kind,backend,value,min,max});
      }
      if (parts.length===3 && new Set(parts.map(p=>p.kind)).size===3)
        result.set(node.backendDOMNodeId,{kind:"date",parts,verified:!!node.evanderInput,
          readonly:!!node.evanderInput?.readonly,min:node.evanderInput?.min,max:node.evanderInput?.max,step:node.evanderInput?.step,stepBase:node.evanderInput?.stepBase});
    } else if (descendants.some(c=>c.role?.value==="MenuListPopup")) {
      const options = descendants.filter(c=>!c.ignored && c.role?.value==="option").map(c=>({
        name:optionLabel(c.name?.value),disabled:property(c,"disabled")===true,backend:c.backendDOMNodeId,
      }));
      if (options.length && options.length<=100 && options.every(o=>o.name.length<=200 && Number.isSafeInteger(o.backend)))
        result.set(node.backendDOMNodeId,{kind:"select",options});
    }
  }
  return result;
}

export function dateValue(parts: DatePart[]): string {
  const value = Object.fromEntries(parts.map(p=>[p.kind,p.value]));
  const candidate = String(value.year).padStart(4,"0")+"-"+String(value.month).padStart(2,"0")+"-"+String(value.day).padStart(2,"0");
  return validDate(candidate) ? candidate : "";
}

export function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year,month,day] = value.split("-").map(Number);
  const leap = year%4===0 && (year%100!==0 || year%400===0);
  return year>=1 && month>=1 && month<=12 && day>=1 && day<=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31][month-1];
}

export function nativeIdentity(control: NativeControl | undefined, afterDateAssignment = false): string {
  // Chromium recreates its UA date segments when the parent's value changes.
  // Only a completed assignment may ignore those child references; the parent
  // reference and every semantic constraint still have to match.
  return JSON.stringify(control?.kind==="date" ? {...control,parts:control.parts.map(({value,backend,...identity})=>
    afterDateAssignment ? identity : {...identity,backend})} : control);
}

export function selectOption(control: NativeControl, text: string) {
  if (control.kind!=="select" || !text || text.length>200) throw new Error("unsupported_target");
  const matches=control.options.filter(o=>!o.disabled && o.name===text);
  if (matches.length!==1) throw new Error("ambiguous_option");
  return matches[0];
}
