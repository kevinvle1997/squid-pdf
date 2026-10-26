// The editor on a touch screen, as a phone opens it: the bar offers no keys, since there are none to press.
import { fileURLToPath } from "node:url";
import { expect, type Page } from "@playwright/test";
import { test } from "./policy";

const SAMPLE = fileURLToPath(new URL("../../fixtures/sample.pdf", import.meta.url));

async function open(page: Page) {
  await page.goto("/");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose a PDF" }).click();
  await (await chooser).setFiles(SAMPLE);
  await expect(page.getByRole("region", { name: "Page 1" })).toBeVisible();
  // Export first, so the bar is drawn before anything in it is looked for.
  await expect(page.getByRole("button", { name: /^Export/ })).toBeVisible();
}

test.describe("on a touch screen", () => {
  // A phone by what it has, not a device's descriptor, which would pin the browser too.
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });

  test("the bar lists no shortcuts, ? opens none, and Export carries no key", async ({ page }) => {
    await open(page);
    expect(await page.evaluate(() => matchMedia("(pointer: coarse)").matches)).toBe(true);
    await expect(page.getByRole("button", { name: "Keyboard shortcuts", includeHidden: true })).toHaveCount(0);
    await page.keyboard.press("?");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // The keycap is there for a keyboard, and found hidden rather than not found at all.
    const keycap = page.getByRole("button", { name: /^Export/ }).locator("kbd");
    await expect(keycap).toBeAttached();
    await expect(keycap).toBeHidden();
  });
});

// The same names with a mouse: a control renamed away would be missed above, and prove nothing.
test.describe("with a mouse", () => {
  test("the bar lists the shortcuts, ? opens them, and Export carries its key", async ({ page }) => {
    await open(page);
    await expect(page.getByRole("button", { name: "Keyboard shortcuts" })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Export/ }).locator("kbd")).toBeVisible();
    await page.keyboard.press("?");
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
  });
});
