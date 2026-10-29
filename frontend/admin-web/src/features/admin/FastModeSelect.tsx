import { fastModeLabel, type FastMode } from "./fastMode";

type Props = {
  value: string;
  onChange: (value: string) => void;
  harness: string;
  route?: string | null;
  prompt?: boolean;
  inheritedFastMode?: FastMode | null;
  disabled?: boolean;
};

export function FastModeSelect({
  value, onChange, harness, route, prompt, inheritedFastMode, disabled,
}: Props) {
  const supported = harness === "codex" && (!route || route === "openai_public");
  return (
    <label className="field-stack">
      <span>{prompt ? "Speed for this prompt" : "Default speed"}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        <option value="">
          {prompt ? `Session default (${fastModeLabel(inheritedFastMode)})` : "Standard (default)"}
        </option>
        <option value="false">Standard</option>
        <option value="true" disabled={!supported}>Fast</option>
        <option value="ultrafast" disabled={!supported}>Ultrafast</option>
      </select>
      <span className="field-description">
        {supported
          ? "Fast and Ultrafast use more allowance or credits and require a supported model and account."
          : "Fast and Ultrafast are available for Codex using public OpenAI."}
      </span>
    </label>
  );
}
