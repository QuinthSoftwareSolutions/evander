// Case membership includes follow-up/correction lineage, but never dependencies.
export function caseTaskIds(tasks, caseId) {
  const members = new Set(tasks.filter((task) => task.case_id === caseId).map((task) => task.id));
  const children = new Map();
  for (const task of tasks) {
    for (const source of new Set([task.parent_task_id, task.originating_task_id].filter(Boolean))) {
      if (!children.has(source)) children.set(source, []);
      children.get(source).push(task.id);
    }
  }
  const pending = [...members];
  for (let index = 0; index < pending.length; index += 1) {
    for (const child of children.get(pending[index]) || []) {
      if (!members.has(child)) {
        members.add(child);
        pending.push(child);
      }
    }
  }
  return members;
}

export function caseBoardPermalink(baseUrl, caseId, { teamId = "", projectId = "" } = {}) {
  const url = new URL(baseUrl);
  url.search = "";
  const params = new URLSearchParams({ view: "task-board" });
  if (caseId) params.set("case_id", caseId);
  if (teamId) params.set("team_id", teamId);
  if (projectId) params.set("project_id", projectId);
  url.hash = `addon:team-addon?${params}`;
  return url.href;
}

export function replaceCaseBoardLocation(browserWindow, caseId, { teamId = "", projectId = "" } = {}) {
  const local = new URL(browserWindow.location.href);
  local.hash = "";
  local.searchParams.set("view", "task-board");
  if (caseId) local.searchParams.set("case_id", caseId);
  else local.searchParams.delete("case_id");
  for (const [name, value] of [["team_id", teamId], ["project_id", projectId]]) {
    if (value) local.searchParams.set(name, value);
    else local.searchParams.delete(name);
  }
  browserWindow.history.replaceState(null, "", local);
  const parent = browserWindow.parent;
  if (parent !== browserWindow) {
    const oldURL = parent.location.href;
    const newURL = caseBoardPermalink(oldURL, caseId, { teamId, projectId });
    if (oldURL !== newURL) {
      parent.history.replaceState(null, "", newURL);
      // The shell reloads the iframe on hashchange. Include its Team/project context
      // so ordinary board selection and clearing a case survive that reload.
      parent.dispatchEvent(new parent.HashChangeEvent("hashchange", { oldURL, newURL }));
    }
  }
}
