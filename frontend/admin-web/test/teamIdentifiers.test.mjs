import assert from "node:assert/strict";
import test from "node:test";
import { identifierSegments, renderIdentifierText } from "../../../src/evander_team_addon/ui_static/team-identifiers.js";

const sha = "0123456789abcdef0123456789abcdef01234567";
const uuid = "3f706adc-8d41-4f2a-b93c-2d57c832eb64";
const task = "task-release-review-3f706adc8d414f2ab93c2d57c832eb64";

test("identifier badges retain exact values and surrounding narrative punctuation", () => {
  const segments = identifierSegments(`Published ${sha}, reviewed ${task}. Trace (${uuid}).`);
  assert.deepEqual(segments.filter(segment => segment.type === "identifier").map(segment => segment.value), [sha, task, uuid]);
  assert.equal(segments.map(segment => segment.value).join(""), `Published ${sha}, reviewed ${task}. Trace (${uuid}).`);
  const explicit = "gitctx-wake-0123456789abcdef01234567";
  assert.deepEqual(identifierSegments(`Review \`${explicit}\` next.`), [
    { type: "text", value: "Review " }, { type: "identifier", value: explicit }, { type: "text", value: " next." },
  ]);
});

test("links, paths, commands, code fences, and readable names stay literal", () => {
  for (const source of [
    `https://example.test/commits/${sha}`, `refs/reviews/${task}`, `C:\\checkouts\\${uuid}`,
    `HTTPS://example.test/?commit=${sha}`, `ssh://example.test/?id=${uuid}`, `//example.test/?id=${uuid}`,
    `/${sha}`, `./${sha}`, `--${sha}`, `\\${sha}`, `${sha}é`, `é${sha}`,
    `\`git show ${sha}\``, `\`OperationStartCommandHandler\``,
    `\`\`echo \`${sha}\`\`\``, `\`git show\n${sha}\``,
    "`container_worker_base_url`", "`team_get_current_dispatch_context`",
    `\`\`\`sh\ngit show ${sha}\n\`\`\``, `~~~text\n${uuid}\n~~~`,
    "Version 2026.09.26 and release-readiness", `prefix${sha}`, `${sha}.json`,
  ]) {
    assert.deepEqual(identifierSegments(source), [{ type: "text", value: source }]);
  }
});

test("narrative markup is escaped and badge attributes cannot inject HTML", () => {
  const rendered = renderIdentifierText(`<img src=x onerror=alert(1)> ${sha} & \"details\"`);
  assert.ok(rendered.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.ok(!rendered.includes("<img"));
  assert.ok(rendered.includes(`data-identifier-value="${sha}"`));
  assert.ok(rendered.includes(`aria-label="Copy SHA: ${sha}"`));
  assert.ok(rendered.includes("&amp; &quot;details&quot;"));
});
