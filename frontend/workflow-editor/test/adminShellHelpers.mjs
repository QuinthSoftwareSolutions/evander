export async function ensureSectionsExpanded(page) {
  await page.locator('nav[aria-label="Admin sections"]').waitFor({ state: "visible" });
  const expandSectionsButton = page.getByRole("button", { name: "Expand sections" });
  if ((await expandSectionsButton.count()) > 0) {
    await expandSectionsButton.first().click();
  }
}
