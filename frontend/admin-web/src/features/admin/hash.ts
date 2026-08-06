export function readAdminHashQuery(hash = window.location.hash): URLSearchParams {
  const raw = hash.replace(/^#/, "").trim();
  const query = raw.split("?")[1] ?? "";
  return new URLSearchParams(query);
}
