type Props = {
  value: string;
  onChange: (value: string) => void;
  harness: string;
  route?: string | null;
  prompt?: boolean;
  inheritedFastMode?: boolean | null;
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
          {prompt ? `Session default (${inheritedFastMode ? "Fast" : "Standard"})` : "Standard (default)"}
        </option>
        <option value="false">Standard</option>
        <option value="true" disabled={!supported}>Fast</option>
      </select>
      <span className="field-description">
        {supported
          ? "Fast uses more allowance or credits and requires a supported model."
          : "Fast is available for Codex using public OpenAI."}
      </span>
    </label>
  );
}
