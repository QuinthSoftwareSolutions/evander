// Shared host context. Add-ons own their controls; server authorization stays authoritative.
const root = document.documentElement;
root.dataset.adminDesign = "";
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
let hostTheme = false;
let privilegedUnlocked = new URLSearchParams(window.location.search).get("privileged_unlocked") === "true";
const privilegeListeners = new Set();

export function onAddonPrivileges(listener) {
  privilegeListeners.add(listener);
  listener(privilegedUnlocked);
  return () => privilegeListeners.delete(listener);
}

function stored(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function applyAppearance(data) {
  if (data.theme === "light" || data.theme === "dark") {
    root.dataset.theme = data.theme;
    root.style.colorScheme = data.theme;
  }
  if (data.density === "compact" || data.density === "comfortable") root.dataset.density = data.density;
}

applyAppearance({
  theme: stored("admin-web:theme") ?? (systemTheme.matches ? "dark" : "light"),
  density: stored("admin-web:density") ?? "compact",
});

systemTheme.addEventListener("change", (event) => {
  if (!hostTheme && !stored("admin-web:theme")) applyAppearance({ theme: event.matches ? "dark" : "light" });
});

window.addEventListener("message", (event) => {
  if (window.parent === window || event.source !== window.parent || event.origin !== window.location.origin) return;
  const data = event.data;
  if (!data || typeof data !== "object" || data.type !== "admin-addon-context") return;
  hostTheme = data.theme === "light" || data.theme === "dark";
  applyAppearance(data);
  if (typeof data.privileged_operations?.unlocked === "boolean") {
    privilegedUnlocked = data.privileged_operations.unlocked;
    for (const listener of privilegeListeners) listener(privilegedUnlocked);
  }
});

if (window.parent !== window) window.parent.postMessage({ type: "admin-addon-ready" }, window.location.origin);
