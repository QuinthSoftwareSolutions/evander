export type ReasoningEffortCatalog = {
  harness_levels: Record<string, string[]>;
  model_rules: Array<{ pattern: string; levels: string[] }>;
  harness_model_rules?: Record<string, Array<{ pattern: string; levels: string[] }>>;
};

export function reasoningEffortOptions(
  catalog: ReasoningEffortCatalog | null | undefined,
  harness: string,
  model: string | null | undefined,
  route?: string | null,
) {
  let levels = catalog?.harness_levels[harness] ?? [];
  const rules = [...(catalog?.harness_model_rules?.[harness] ?? []), ...(catalog?.model_rules ?? [])];
  const rule = route === "local_openai" ? undefined : rules.find(
    (candidate) => new RegExp(candidate.pattern).test((model ?? "").toLowerCase()),
  );
  if (rule) levels = levels.filter((level) => rule.levels.includes(level));
  if (route === "openrouter") levels = levels.filter((level) => level !== "ultra");
  return { levels, known: !!rule };
}
