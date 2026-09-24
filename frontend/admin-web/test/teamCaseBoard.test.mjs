import assert from "node:assert/strict";
import test from "node:test";
import {
  caseTaskIds,
  caseBoardPermalink,
  replaceCaseBoardLocation,
  taskBoardViewSearchParams,
} from "../../../src/evander_team_addon/ui_static/team-case-board.js";
import { addonIframeHashQuery, nextPathFromHash } from "../src/adminRouting.ts";

test("case board follows children and correction origins, not dependencies or unrelated cases", () => {
  const tasks = [
    { id: "correction-child", parent_task_id: "correction" },
    { id: "root", case_id: "intake", dependency_task_ids: ["unrelated"] },
    { id: "child", parent_task_id: "root" },
    { id: "correction", case_id: "review-case", originating_task_id: "child" },
    { id: "done", state: "finished", case_id: "intake" },
    { id: "unrelated", case_id: "other" },
    { id: "unrelated-dependent", dependency_task_ids: ["root"] },
    { id: "cycle-a", parent_task_id: "cycle-b" },
    { id: "cycle-b", parent_task_id: "cycle-a" },
  ];
  assert.deepEqual([...caseTaskIds(tasks, "intake")].sort(), ["child", "correction", "correction-child", "done", "root"]);
  assert.equal(caseTaskIds(tasks, "missing").size, 0);
  tasks[7].case_id = "cyclic";
  assert.equal(caseTaskIds(tasks, "cyclic").size, 2);
});

test("case permalink encodes IDs, drops privileged query values, and survives shell/login routing", () => {
  const link = caseBoardPermalink("https://evander.test/?privileged_unlocked=true#workflow", "case A&42");
  const url = new URL(link);
  assert.equal(url.search, "");
  assert.equal(url.hash, "#addon:team-addon?view=task-board&case_id=case+A%2642");
  assert.equal(nextPathFromHash(url.hash), `/${url.hash}`);
  const forwarded = new URLSearchParams(addonIframeHashQuery(url.hash, "addon:team-addon"));
  assert.equal(forwarded.get("view"), "task-board");
  assert.equal(forwarded.get("case_id"), "case A&42");
  assert.equal(addonIframeHashQuery(url.hash, "addon:another-addon"), "");
});

test("task board view requests carry the selected server scope", () => {
  const params = taskBoardViewSearchParams({
    caseId: "case A&42",
    teamId: "team A",
    projectId: "project&1",
  });
  assert.equal(
    params.toString(),
    "scope=selection&case_id=case+A%2642&team_id=team+A&project_id=project%261",
  );
  assert.equal(taskBoardViewSearchParams().toString(), "scope=selection");
});

test("embedded case selection updates shell routing without reloading the board", () => {
  function mockWindow(href) {
    const events = [];
    const win = {
      location: new URL(href),
      events,
      history: { replaceState(_state, _title, url) { win.location = new URL(url); } },
      dispatchEvent(event) { events.push(event); },
    };
    win.parent = win;
    return win;
  }
  const shell = mockWindow("https://evander.test/#addon:team-addon?view=task-board&case_id=old");
  const board = mockWindow("https://evander.test/addon/index.html?directory_path=root&case_id=old#case_id=stale");
  board.parent = shell;
  const context = { teamId: "team A", projectId: "project&1" };
  replaceCaseBoardLocation(board, "new case", context);
  assert.equal(board.location.hash, "");
  assert.equal(board.location.searchParams.get("case_id"), "new case");
  assert.equal(board.location.searchParams.get("directory_path"), "root");
  assert.equal(shell.location.hash, "#addon:team-addon?view=task-board&case_id=new+case&team_id=team+A&project_id=project%261");
  assert.deepEqual(shell.events, []);
  replaceCaseBoardLocation(board, "new case", context);
  assert.deepEqual(shell.events, []);
  replaceCaseBoardLocation(board, "", context);
  assert.equal(board.location.searchParams.has("case_id"), false);
  assert.equal(shell.location.hash, "#addon:team-addon?view=task-board&team_id=team+A&project_id=project%261");
  const forwarded = new URLSearchParams(addonIframeHashQuery(shell.location.hash, "addon:team-addon"));
  assert.equal(forwarded.get("team_id"), context.teamId);
  assert.equal(forwarded.get("project_id"), context.projectId);
  assert.equal(board.location.searchParams.get("team_id"), context.teamId);
  assert.equal(board.location.searchParams.get("project_id"), context.projectId);
  assert.deepEqual(shell.events, []);
  board.parent = board;
  replaceCaseBoardLocation(board, "standalone");
  assert.equal(board.location.searchParams.get("case_id"), "standalone");
  assert.equal(board.events.length, 0);
});
