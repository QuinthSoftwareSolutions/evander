import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const [runtimePathArgument, browsersPathArgument] = process.argv.slice(2);

if (!runtimePathArgument || !browsersPathArgument) {
  console.error(
    "Usage: node verify_seeded_playwright_runtime.mjs <playwright-node-modules> <browser-cache>",
  );
  process.exit(2);
}

const runtimePath = path.resolve(runtimePathArgument);
const browsersPath = path.resolve(browsersPathArgument);
process.env.PLAYWRIGHT_BROWSERS_PATH = browsersPath;
process.env.PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";

let executablePath;
try {
  const require = createRequire(import.meta.url);
  const { chromium } = require(path.join(runtimePath, "playwright"));
  executablePath = chromium.executablePath();
} catch (error) {
  console.error("Seeded Playwright runtime preflight failed.");
  console.error(`Runtime: ${runtimePath}`);
  console.error(`Browser cache: ${browsersPath}`);
  console.error(
    `The seeded playwright package could not resolve Chromium: ${
      error instanceof Error ? error.message : String(error)
    }`,
  );
  process.exit(3);
}

let executable = false;
try {
  fs.accessSync(executablePath, fs.constants.X_OK);
  executable = true;
} catch {
  // The actionable mismatch report below includes the expected and available builds.
}

if (!executable) {
  let availableEntries = [];
  try {
    availableEntries = fs
      .readdirSync(browsersPath, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .filter(
        (name) =>
          name.startsWith("chromium-") || name.startsWith("chromium_headless_shell-"),
      )
      .sort();
  } catch {
    // A missing/unreadable cache is reported as having no Chromium entries.
  }

  console.error("Seeded Playwright runtime/browser mismatch.");
  console.error(`Runtime: ${runtimePath}`);
  console.error(`Browser cache: ${browsersPath}`);
  console.error(`Playwright expects executable: ${executablePath}`);
  console.error(
    `Available Chromium cache entries: ${
      availableEntries.length > 0 ? availableEntries.join(", ") : "(none)"
    }`,
  );
  console.error(
    "Re-seed the Playwright package and browser cache from the same Playwright installation; browser downloads are disabled for worker jobs.",
  );
  process.exit(4);
}

process.stdout.write(`${executablePath}\n`);
