import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../../..", import.meta.url));
const design = path.join(root, "frontend/admin-web/src/design");
const addons = {
  team: ["team-ui.css", "team-org-flow.css", "team-agent-create.css"],
  bokio: ["bokio-ui.css"],
  skatteverket: ["skatteverket-ui.css"],
  mssql_seed: ["mssql-seed-ui.css"],
  miro: ["miro.css"],
  figma: ["figma.css"],
};
const banner = "/* Generated from frontend/admin-web/src/design. Run npm run sync:design. */\n";
const css = (await Promise.all(["tokens.css", "controls.css", "addons.css", "team.css", "integrations.css"].map(name => readFile(path.join(design, name), "utf8")))).join("\n");
const script = "// Generated from frontend/admin-web/src/design/addon-context.js.\n" + await readFile(path.join(design, "addon-context.js"), "utf8");
const check = process.argv.includes("--check");
for (const [addon, structuralStyles] of Object.entries(addons)) {
  const directory = path.join(root, `src/evander_${addon}_addon/ui_static`);
  const styles = banner + structuralStyles.map(name => `@import url("./${name}") layer(admin-addon-structure);`).join("\n") + "\n" + css;
  for (const [name, contents] of [["admin-design.css", styles], ["admin-design.js", script]]) {
    const destination = path.join(directory, name);
    if (check) {
      if (await readFile(destination, "utf8").catch(() => null) !== contents) throw new Error(`${destination} is stale; run npm run sync:design`);
    } else {
      await writeFile(destination, contents);
    }
  }
}
console.log(`Admin design assets ${check ? "verified" : "synchronized"} for ${Object.keys(addons).length} add-ons.`);
