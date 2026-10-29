export async function ensureSectionsExpanded(page) {
  await page.locator('nav[aria-label="Admin sections"]').waitFor({ state: "visible" });
  const expandSectionsButton = page.getByRole("button", { name: "Expand sections" });
  if ((await expandSectionsButton.count()) > 0) {
    await expandSectionsButton.first().click();
  }
}

export async function fulfillAdminShellBackgroundApiRequest(route, pathname) {
  if (pathname === "/api/admin/branding") {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "{}",
    });
    return true;
  }
  if (pathname.startsWith("/api/admin/branding/assets/")) {
    await route.fulfill({ status: 204, body: "" });
    return true;
  }
  if (pathname === "/api/admin/user-notifications") {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "admin",
        unread_count: 0,
        notifications: [],
      }),
    });
    return true;
  }
  if (pathname === "/api/admin/user-preferences/workflow-tutorial-progress") {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ completed_tutorial_ids: [], skipped_tutorial_ids: [] }),
    });
    return true;
  }
  return false;
}
