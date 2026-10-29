# Workflow editor boundaries

`App.tsx` coordinates state, derived canvas views, subscriptions, and document
persistence. Components and feature factories receive explicit typed values and
actions. They keep the editor's existing mutation guards and request paths.

- `graphModel.ts` owns document normalization, graph membership, copies, merges, and connection validation.
- `nodeDefaults.ts` creates default node configurations.
- `bindingValidation.ts` owns client binding diagnostics and validation cache helpers.
- `fieldModels.tsx` owns field values, draft conversion, and reusable field controls.
- `canvasModel.tsx` translates documents into canvas nodes, edges, cards, and positions.
- `runDetailsModel.ts` owns run lookup/filter construction and recorded graph projections.
- `tutorialModel.ts` owns tutorial progress normalization and canvas presentation helpers.
- `editorApi.ts` owns the editor request adapter and endpoint query construction.
- `useWorkflowWorkbench.ts` owns the horizontal split, view/tab preferences, and pointer/keyboard resize cleanup.
- `mapTemplateDraft.ts` converts typed map rows without losing value types; `MapTemplateField.tsx` edits these rows and switches to raw JSON through the existing inspector draft path.
- `useNodeExecutionHistory.ts` owns history queries, filter/date state, pagination, and selection guards.

The `*Actions.ts` modules group canvas navigation, connections, selection, node
drafts, field edits, tutorials, generation, and pack management. Each factory binds
its functions to the current render's typed inputs. App forwarding declarations
let features call each other while retaining that render's closures. Factories
contain no hooks and must be recreated for each render; state and refs stay with
the coordinator or their owning hook.

The `*Renderers.tsx` modules group field controls and the desktop/compact panels. Node configuration, outputs, and history share
one tabbed inspector; inactive tabs remain mounted to preserve in-progress input.
Named components own policy fields, metadata rules, run inspection, execution
history, operator steering, generated draft preview, and pack history presentation.
Desktop and compact markup remain distinct where their classes or layout differ.

Model helpers do not import `App.tsx` or feature components. Action and rendering
modules invoke their supplied callbacks so editing, tutorial, and inspection
permissions retain the same entry points.
