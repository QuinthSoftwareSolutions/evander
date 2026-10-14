import assert from "node:assert/strict";
import test from "node:test";
import { createMapTemplateDraftRow, mapTemplateDraftRowsFromValue, mapTemplateDraftRowsToValue, parseMapTemplateJson } from "../src/features/workflow-editor/mapTemplateDraft.ts";

test("mapping rows preserve typed constants, complex values, and exact references", () => {
  const rows = [
    { id: "numeric", name: "count", kind: "number", text: "2e1" },
    { id: "text", name: "code", kind: "string", text: "0020" },
    { id: "false", name: "enabled", kind: "boolean", text: "false" },
    { id: "null", name: "empty", kind: "null", text: "" },
    { id: "object", name: "details", kind: "object", text: '{"nested":[true,null,"${{source.value}}"]}' },
    { id: "array", name: "items", kind: "array", text: '[1,"2",false]' },
    { id: "binding", name: "reference", kind: "binding", text: '${{source.value ?? "default"}}' },
    { id: "template", name: "title", kind: "template", text: "Task ${{source.title}}" },
    { id: "prototype", name: "__proto__", kind: "string", text: "ordinary output" },
  ];
  const result = mapTemplateDraftRowsToValue(rows);
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.value, JSON.parse('{"count":20,"code":"0020","enabled":false,"empty":null,"details":{"nested":[true,null,"${{source.value}}"]},"items":[1,"2",false],"reference":"${{source.value ?? \\"default\\"}}","title":"Task ${{source.title}}","__proto__":"ordinary output"}'));
});

test("unfinished and invalid rows block serialization instead of losing fields or coercing values", () => {
  for (const [name, kind, text] of [["", "string", "value"], ["count", "number", ""], ["count", "number", "1e999"], ["count", "number", "null"], ["data", "object", "[]"], ["data", "object", '{"count":1e999}'], ["items", "array", "{}"], ["items", "array", "[1e999]"], ["source", "binding", "source.value"], ["enabled", "boolean", "yes"]]) {
    const result = mapTemplateDraftRowsToValue([{id:"bad",name,kind,text}]);
    assert.equal(result.value, null, `${name}/${kind}/${text}`);
    assert.equal(result.errors.length, 1);
  }
  const duplicate = mapTemplateDraftRowsToValue([createMapTemplateDraftRow("value",1),createMapTemplateDraftRow("value",2)]);
  assert.equal(duplicate.value,null);
  assert.match(duplicate.errors[0].message,/Duplicate/);
});

test("saved maps select useful controls while unusual templates stay in the JSON view", () => {
  const rows = mapTemplateDraftRowsFromValue({ binding:'${{source.value ?? 0}}', text:"Task ${{source.title}}", number:0, boolean:false, nil:null, object:{ nested:1 }, array:[], literal:"" });
  assert.deepEqual(rows.map(row=>row.kind),["binding","template","number","boolean","null","object","array","string"]);
  assert.equal(rows.at(-1).text,"");
  for(const value of [null,[],"${{source.object}}",{ "":1 }])assert.equal(mapTemplateDraftRowsFromValue(value),null);
  assert.deepEqual(mapTemplateDraftRowsToValue([]).value,{});
  assert.deepEqual(parseMapTemplateJson('{"":false}'),{"":false});
  for (const draft of ['[]','42','null','{"count":1e999}']) assert.throws(()=>parseMapTemplateJson(draft));
});
