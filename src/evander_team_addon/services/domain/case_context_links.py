"""Conservative lineage retention for generated correction work."""


def link_release_correction_case(state, task, release):
    if task.case_id:
        return
    source_tasks = {
        state.deliverables[d].task_id for d in release.deliverable_ids if d in state.deliverables
    }
    cases = {state.tasks[t].case_id for t in source_tasks if t in state.tasks}
    if len(cases) != 1 or None in cases:
        return
    case_id = next(iter(cases))
    case = state.cases.get(case_id)
    if case is None or case.project_id != task.project_id:
        return
    task.case_id = case.id
    if task.id not in case.task_ids:
        case.task_ids.append(task.id)
    if len(source_tasks) == 1 and not task.originating_task_id:
        task.originating_task_id = next(iter(source_tasks))
