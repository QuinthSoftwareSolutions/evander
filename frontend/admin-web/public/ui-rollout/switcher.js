// Temporary rollout control shared by both complete UI bundles. See classic/README.md.
const version = document.querySelector("script[data-admin-ui-version]")?.dataset.adminUiVersion;
if (version === "classic" || version === "new") {
  // Make the current tab explicit; changing the remembered preference cannot change this tab on reload.
  const current = new URL(location.href);
  if (current.searchParams.get("remember_admin_ui") === "1") {
    // Remember only after navigation succeeds, including any native unsaved-change guard.
    document.cookie = `admin_ui_version=${version}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
  }
  current.searchParams.delete("remember_admin_ui");
  current.searchParams.set("admin_ui", version);
  history.replaceState(history.state, "", current);

  const control = document.createElement("label");
  control.className = "admin-ui-version-control";
  const label = document.createElement("span");
  label.textContent = "UI";
  const select = document.createElement("select");
  select.setAttribute("aria-label", "UI version");
  for (const [value, text] of [["classic", "Classic"], ["new", "New UI"]]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    select.append(option);
  }
  select.value = version;
  control.append(label, select);
  select.addEventListener("change", () => {
    const next = select.value;
    select.value = version;
    if (next === version) return;
    const name = next === "classic" ? "Classic" : "New UI";
    if (!window.confirm(`Switch to ${name}? Save any unsaved changes before switching. The page will reload.`)) return;
    const destination = new URL(location.href);
    destination.searchParams.set("admin_ui", next);
    destination.searchParams.set("remember_admin_ui", "1");
    location.assign(destination.href);
  });

  function attach() {
    const group = document.querySelector(".admin-shell-header .admin-shell-action-group");
    if (group && control.parentElement !== group) {
      control.classList.remove("admin-ui-version-fallback");
      group.prepend(control);
    }
  }
  new MutationObserver(attach).observe(document.body, { childList: true, subtree: true });
  attach();
  // The way back to Classic must also work when the selected application fails to start.
  setTimeout(() => {
    if (!control.isConnected) {
      control.classList.add("admin-ui-version-fallback");
      document.body.append(control);
    }
  }, 1000);
}
