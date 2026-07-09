import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/features/skills/SkillsPage.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("skills page surfaces workflow references from delete conflicts", () => {
  assert.match(source, /import \{ ApiError, fetchJson, formatTimestamp \} from "\.\.\/admin\/api";/);
  assert.match(source, /function skillDeleteConflictFromError\(error: unknown\): SkillDeleteConflictState \| null/);
  assert.match(source, /error\.status !== 409/);
  assert.match(source, /details\.references/);
  assert.match(source, /setDeleteConflict\(skillDeleteConflictFromError\(reason\)\);/);
  assert.match(source, /className="skill-delete-conflict"/);
  assert.match(source, /Workflow references/);
  assert.match(source, /workflowReferenceLocation\(reference\)/);
  assert.match(source, /Pack \$\{reference\.workflow_pack\}/);
  assert.match(source, /Inline workflow config/);
  assert.match(styles, /\.skill-delete-reference-list li \{/);
});
