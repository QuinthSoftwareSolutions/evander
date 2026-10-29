import { build } from "esbuild";
import { cp, mkdir } from "node:fs/promises";
await mkdir("evander_browser_sessions_addon/ui_static", { recursive: true });
await mkdir("evander_browser_sessions_addon/controller", { recursive: true });
await mkdir("dist/extension", { recursive: true });
for (const [entry, outfile, platform] of [
  ["shared/engine.ts", "dist/engine.mjs", "browser"],
  ["web/capture-worklet.ts", "evander_browser_sessions_addon/ui_static/capture-worklet.js", "browser"],
  ["web/capture-worklet.ts", "dist/extension/capture-worklet.js", "browser"],
  ["web/voice-page.ts", "evander_browser_sessions_addon/ui_static/voice.js", "browser"],
  ["web/viewer.ts", "evander_browser_sessions_addon/ui_static/viewer.js", "browser"],
  ["web/styles.css", "evander_browser_sessions_addon/ui_static/styles.css", "browser"],
  ["controller/automation.ts", "dist/automation.mjs", "node"],
  ["controller/commands.ts", "dist/commands.mjs", "node"],
  ["controller/connection.ts", "dist/connection.mjs", "node"],
  ["controller/hosted.ts", "evander_browser_sessions_addon/controller/hosted.mjs", "node"],
  ["extension/background.ts", "dist/extension/background.js", "browser"],
  ["extension/capture.ts", "dist/extension/capture.js", "browser"],
  ["extension/panel.ts", "dist/extension/panel.js", "browser"],
  ["extension/microphone-page.ts", "dist/extension/microphone.js", "browser"],
]) {
  await build({ entryPoints: [entry], outfile, bundle: true, format: "esm", platform,
    target: platform === "node" ? "node22" : "chrome125", sourcemap: false,
    ...(platform === "node" ? {banner:{js:"import { createRequire as evanderCreateRequire } from 'node:module'; const require = evanderCreateRequire(import.meta.url);"}} : {}) });
}
for (const name of ["index.html", "join.html", "voice.html", "voice.css"])
  await cp("web/" + name, "evander_browser_sessions_addon/ui_static/" + name);
for (const name of ["manifest.json", "panel.html", "microphone.html", "capture.html", "styles.css"])
  await cp("extension/" + name, "dist/extension/" + name);
console.log("Built viewer, browser controller and Chrome extension.");
