export type CyberAccessProgram = "standard" | "daybreak_blue" | "daybreak_red";

type Props = {
  value: string;
  onChange: (value: string) => void;
  harness: string;
  route?: string | null;
  disabled?: boolean;
};

export function CyberAccessProgramSelect({ value, onChange, harness, route, disabled }: Props) {
  const supported = harness === "codex" && (!route || route === "openai_public");
  return (
    <label className="field-stack">
      <span>Cyber access program</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        <option value="">Automatic (provider default)</option>
        <option value="standard" disabled={!supported}>Standard</option>
        <option value="daybreak_blue" disabled={!supported}>Daybreak Blue</option>
        <option value="daybreak_red" disabled={!supported}>Daybreak Red</option>
      </select>
      <span className="field-description">
        {supported
          ? "Applies to subsequent turns. Daybreak requires approval for the selected account and model."
          : "Explicit Cyber programs are available for Codex using public OpenAI."}
      </span>
    </label>
  );
}
