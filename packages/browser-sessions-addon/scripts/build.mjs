import { build } from "esbuild";
import { cp, mkdir } from "node:fs/promises";
await mkdir("evander_browser_sessions_addon/ui_static", { recursive: true });
await mkdir("evander_browser_sessions_addon/controller", { recursive: true });
await mkdir("dist/extension", { recursive: true });
for (const [entry, outfile, platform] of [
  ["shared/engine.ts", "dist/engine.mjs", "browser"],
  ["web/viewer.ts", "evander_browser_sessions_addon/ui_static/viewer.js", "browser"],
  ["controller/hosted.ts", "evander_browser_sessions_addon/controller/hosted.mjs", "node"],
  ["extension/background.ts", "dist/extension/background.js", "browser"],
  ["extension/panel.ts", "dist/extension/panel.js", "browser"],
]) {
  await build({ entryPoints: [entry], outfile, bundle: true, format: "esm", platform,
    target: platform === "node" ? "node22" : "chrome125", sourcemap: false });
}
for (const name of ["index.html", "styles.css", "join.html"])
  await cp("web/" + name, "evander_browser_sessions_addon/ui_static/" + name);
for (const name of ["manifest.json", "panel.html", "styles.css"])
  await cp("extension/" + name, "dist/extension/" + name);
console.log("Built viewer, browser controller and Chrome extension.");
