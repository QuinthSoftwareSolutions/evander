import { useId } from "react";

import { reasoningEffortOptions, type ReasoningEffortCatalog } from "./reasoningEffort";

type ReasoningEffortSelectProps = {
  catalog?: ReasoningEffortCatalog | null;
  harness: string;
  model?: string | null;
  route?: string | null;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  prompt?: boolean;
  inheritedEffort?: string | null;
};

export function ReasoningEffortSelect({
  catalog, harness, model, route, value, onChange, disabled, prompt = false, inheritedEffort,
}: ReasoningEffortSelectProps) {
  const id = useId();
  const { levels, known } = reasoningEffortOptions(catalog, harness, model, route);
  const invalid = !!catalog && !!value && !levels.includes(value);
  const inherited = inheritedEffort ?? (harness === "codex" ? "high" : "provider default");
  const description = invalid
    ? "Choose a supported effort or clear the override."
    : catalog && !known
      ? "Model support is unverified. The selected level is sent to the provider."
      : known && !levels.length
        ? "This model does not support a reasoning level."
        : null;

  return (
    <div className="field-stack">
      <label htmlFor={id}>Reasoning effort</label>
      <select
        id={id}
        value={value}
        aria-describedby={description ? `${id}-description` : undefined}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled || !catalog || (known && !levels.length && !value)}
      >
        <option value="">
          {prompt ? `Use session default (${inherited})` : harness === "codex" ? "Default (high)" : "Provider default"}
        </option>
        {invalid ? <option value={value}>{value} (unsupported)</option> : null}
        {levels.map((level) => <option key={level} value={level}>{level}</option>)}
      </select>
      {description ? (
        <span id={`${id}-description`} className="field-description" role={invalid ? "alert" : undefined}>
          {description}
        </span>
      ) : null}
    </div>
  );
}
