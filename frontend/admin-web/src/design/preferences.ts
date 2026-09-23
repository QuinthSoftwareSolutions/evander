export type AdminDensity = "compact" | "comfortable";
export const ADMIN_DENSITY_STORAGE_KEY = "admin-web:density";

export function initialAdminDensity(): AdminDensity {
  try {
    return window.localStorage.getItem(ADMIN_DENSITY_STORAGE_KEY) === "comfortable" ? "comfortable" : "compact";
  } catch {
    return "compact";
  }
}

export function applyAdminDensity(density: AdminDensity): void {
  document.documentElement.dataset.density = density;
  document.documentElement.dataset.adminDesign = "";
  try {
    window.localStorage.setItem(ADMIN_DENSITY_STORAGE_KEY, density);
  } catch {
    // Browser storage restrictions must not prevent a local appearance preference.
  }
}
