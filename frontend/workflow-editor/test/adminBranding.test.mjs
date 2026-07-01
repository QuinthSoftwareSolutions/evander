import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  DEFAULT_ADMIN_BRANDING,
  EMPTY_ADMIN_BRANDING,
  initialAdminBranding,
  normalizeAdminBranding,
  themedLogoUrl,
} from "../src/adminBranding.ts";

test("static source HTML does not ship a default title", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /<title><\/title>/);
  assert.doesNotMatch(html, /evander Control Plane Admin/);
});

test("initial branding is absent when no server bootstrap payload exists", () => {
  const previousWindow = globalThis.window;
  Reflect.deleteProperty(globalThis, "window");
  try {
    assert.equal(initialAdminBranding(), null);
  } finally {
    if (previousWindow === undefined) {
      Reflect.deleteProperty(globalThis, "window");
    } else {
      globalThis.window = previousWindow;
    }
  }
  assert.equal(EMPTY_ADMIN_BRANDING.page_title, "");
  assert.equal(EMPTY_ADMIN_BRANDING.header_title, "");
});

test("initial branding reads the server bootstrap payload", () => {
  const previousWindow = globalThis.window;
  globalThis.window = {
    __ADMIN_WEB_BRANDING__: {
      page_title: "Factory Alpha Admin",
      header_title: "Factory Alpha",
      header_title_visible: false,
      header_logo_size: "wordmark",
    },
  };
  try {
    const branding = initialAdminBranding();
    assert.equal(branding?.page_title, "Factory Alpha Admin");
    assert.equal(branding?.header_title, "Factory Alpha");
    assert.equal(branding?.header_title_visible, false);
    assert.equal(branding?.header_logo_size, "wordmark");
  } finally {
    if (previousWindow === undefined) {
      Reflect.deleteProperty(globalThis, "window");
    } else {
      globalThis.window = previousWindow;
    }
  }
});

test("branding normalization keeps stable defaults", () => {
  assert.equal(DEFAULT_ADMIN_BRANDING.page_title, "Evander Admin");
  assert.equal(DEFAULT_ADMIN_BRANDING.header_title, "EVANDER");
  assert.equal(DEFAULT_ADMIN_BRANDING.header_title_visible, false);
  assert.equal(DEFAULT_ADMIN_BRANDING.header_logo_size, "wordmark");
  assert.deepEqual(DEFAULT_ADMIN_BRANDING.product_logo, {
    light_url: "/api/admin/branding/assets/evander-logo-light.png",
    dark_url: "/api/admin/branding/assets/evander-logo-dark.png",
    alt: "Evander Logo",
  });
  assert.deepEqual(DEFAULT_ADMIN_BRANDING.company_logo, {
    light_url: "/api/admin/branding/assets/qss-light.png",
    dark_url: "/api/admin/branding/assets/qss-dark.png",
    alt: "Quinth Software Solutions",
  });
  assert.deepEqual(normalizeAdminBranding(null), DEFAULT_ADMIN_BRANDING);
  assert.deepEqual(normalizeAdminBranding({ page_title: "   ", product_logo: [] }), DEFAULT_ADMIN_BRANDING);
});

test("branding normalization trims text and falls header logo back to product logo", () => {
  const branding = normalizeAdminBranding({
    page_title: " Factory Alpha Admin ",
    header_title: " Factory Alpha ",
    header_title_visible: "false",
    header_logo_size: "wordmark",
    product_logo: {
      light_url: " /api/admin/branding/assets/logo-light.png ",
      dark_url: "",
      alt: " Factory Alpha logo ",
    },
    company_logo: {
      dark_url: "https://assets.example.test/company-dark.png",
      alt: " ",
    },
  });

  assert.equal(branding.page_title, "Factory Alpha Admin");
  assert.equal(branding.header_title, "Factory Alpha");
  assert.equal(branding.header_title_visible, false);
  assert.equal(branding.header_logo_size, "wordmark");
  assert.deepEqual(branding.product_logo, {
    light_url: "/api/admin/branding/assets/logo-light.png",
    dark_url: null,
    alt: "Factory Alpha logo",
  });
  assert.deepEqual(branding.header_logo, branding.product_logo);
  assert.deepEqual(branding.company_logo, {
    light_url: null,
    dark_url: "https://assets.example.test/company-dark.png",
    alt: "Quinth Software Solutions",
  });
});

test("themed logo URL prefers matching theme then configured opposite theme then bundle fallback", () => {
  assert.equal(
    themedLogoUrl({ light_url: "/light.png", dark_url: "/dark.png", alt: "Logo" }, "dark", "bundle-light", "bundle-dark"),
    "/dark.png",
  );
  assert.equal(
    themedLogoUrl({ light_url: "/light.png", dark_url: null, alt: "Logo" }, "dark", "bundle-light", "bundle-dark"),
    "/light.png",
  );
  assert.equal(
    themedLogoUrl({ light_url: null, dark_url: null, alt: "Logo" }, "light", "bundle-light", "bundle-dark"),
    "bundle-light",
  );
});
