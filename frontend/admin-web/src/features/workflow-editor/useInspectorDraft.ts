import { useMemo, useReducer, type Dispatch, type SetStateAction } from "react";
import { initialInspectorDraft, inspectorDraftReducer, type InspectorDraftAction, type InspectorDraftState } from "./inspectorDraftState";

export type InspectorDraftSetters = {
  [K in keyof InspectorDraftState as `set${Capitalize<K>}`]: Dispatch<SetStateAction<InspectorDraftState[K]>>;
};

export type InspectorDraftController = {
  state: InspectorDraftState;
  setters: InspectorDraftSetters;
  replace: (state: InspectorDraftState) => void;
};

/** One owner for the inspector's parsed values, invalid drafts, and display modes. */
export function useInspectorDraft(): InspectorDraftController {
  const [state, dispatch] = useReducer(inspectorDraftReducer, undefined, initialInspectorDraft);
  const setters = useMemo(() => Object.fromEntries(
    (Object.keys(initialInspectorDraft()) as Array<keyof InspectorDraftState>).map((field) => [
      `set${field[0].toUpperCase()}${field.slice(1)}`,
      (value: SetStateAction<InspectorDraftState[typeof field]>) => dispatch({ type: "set", field, value } as InspectorDraftAction),
    ]),
  ) as InspectorDraftSetters, []);
  return { state, setters, replace: (next) => dispatch({ type: "replace", state: next }) };
}
