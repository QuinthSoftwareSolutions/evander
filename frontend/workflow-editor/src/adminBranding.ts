export type AdminLogoConfig = {
  light_url: string | null;
  dark_url: string | null;
  alt: string;
};

export type AdminHeaderLogoSize = "compact" | "wordmark";

export type AdminBrandingConfig = {
  page_title: string;
  header_title: string;
  header_title_visible: boolean;
  header_logo_size: AdminHeaderLogoSize;
  header_logo: AdminLogoConfig;
  product_logo: AdminLogoConfig;
  company_logo: AdminLogoConfig;
};

export const DEFAULT_ADMIN_BRANDING: AdminBrandingConfig = {
  page_title: "evander Control Plane Admin",
  header_title: "evander Control Plane",
  header_title_visible: true,
  header_logo_size: "compact",
  header_logo: {
    light_url: null,
    dark_url: null,
    alt: "evander Control Plane logo",
  },
  product_logo: {
    light_url: null,
    dark_url: null,
    alt: "evander Control Plane logo",
  },
  company_logo: {
    light_url: null,
    dark_url: null,
    alt: "Company logo",
  },
};

export const EMPTY_ADMIN_BRANDING: AdminBrandingConfig = {
  page_title: "",
  header_title: "",
  header_title_visible: true,
  header_logo_size: "compact",
  header_logo: {
    light_url: null,
    dark_url: null,
    alt: "",
  },
  product_logo: {
    light_url: null,
    dark_url: null,
    alt: "",
  },
  company_logo: {
    light_url: null,
    dark_url: null,
    alt: "",
  },
};

type AdminTheme = "light" | "dark";

function asRecord(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as Record<string, unknown>;
}

function optionalText(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim();
  return normalized || null;
}

function textOrDefault(value: unknown, fallback: string): string {
  return optionalText(value) ?? fallback;
}

function booleanOrDefault(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value !== "string") {
    return fallback;
  }
  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) {
    return true;
  }
  if (["0", "false", "no", "off"].includes(normalized)) {
    return false;
  }
  return fallback;
}

function headerLogoSizeOrDefault(value: unknown, fallback: AdminHeaderLogoSize): AdminHeaderLogoSize {
  const normalized = optionalText(value)?.toLowerCase();
  return normalized === "compact" || normalized === "wordmark" ? normalized : fallback;
}

function normalizeLogoConfig(value: unknown, fallback: AdminLogoConfig): AdminLogoConfig {
  const record = asRecord(value);
  const lightUrl = optionalText(record.light_url) ?? fallback.light_url;
  const darkUrl = optionalText(record.dark_url) ?? fallback.dark_url;
  return {
    light_url: lightUrl,
    dark_url: darkUrl,
    alt: textOrDefault(record.alt, fallback.alt),
  };
}

export function normalizeAdminBranding(value: unknown): AdminBrandingConfig {
  const record = asRecord(value);
  const productLogo = normalizeLogoConfig(record.product_logo, DEFAULT_ADMIN_BRANDING.product_logo);
  return {
    page_title: textOrDefault(record.page_title, DEFAULT_ADMIN_BRANDING.page_title),
    header_title: textOrDefault(record.header_title, DEFAULT_ADMIN_BRANDING.header_title),
    header_title_visible: booleanOrDefault(
      record.header_title_visible,
      DEFAULT_ADMIN_BRANDING.header_title_visible,
    ),
    header_logo_size: headerLogoSizeOrDefault(
      record.header_logo_size,
      DEFAULT_ADMIN_BRANDING.header_logo_size,
    ),
    product_logo: productLogo,
    company_logo: normalizeLogoConfig(record.company_logo, DEFAULT_ADMIN_BRANDING.company_logo),
    header_logo: normalizeLogoConfig(record.header_logo, productLogo),
  };
}

export function initialAdminBranding(): AdminBrandingConfig | null {
  if (typeof window === "undefined") {
    return null;
  }
  const payload = (window as Window & { __ADMIN_WEB_BRANDING__?: unknown }).__ADMIN_WEB_BRANDING__;
  if (payload === undefined) {
    return null;
  }
  return normalizeAdminBranding(payload);
}

export function themedLogoUrl(
  logo: AdminLogoConfig,
  theme: AdminTheme,
  fallbackLightUrl: string,
  fallbackDarkUrl: string,
): string {
  if (theme === "dark") {
    return logo.dark_url ?? logo.light_url ?? fallbackDarkUrl;
  }
  return logo.light_url ?? logo.dark_url ?? fallbackLightUrl;
}
