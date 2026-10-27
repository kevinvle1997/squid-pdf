// The editor on a touch screen, as a phone opens it: the bar offers no keys, since there are none to press.
import { fileURLToPath } from "node:url";
import { expect, type Page } from "@playwright/test";
import { test } from "./policy";

const SAMPLE = fileURLToPath(new URL("../../fixtures/sample.pdf", import.meta.url));
// The first page's line with a date in it.
const DATED = "This agreement is made on 14 March 2026 between";

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

  test("a page's delete button is a finger wide, offers no key, and a tap deletes at once", async ({ page }) => {
    await open(page);
    const button = page.getByRole("button", { name: "Delete page 1", exact: true });
    await expect(button).toBeVisible();
    // The hit area grows past the icon drawn, so the hit area is what is measured.
    const hit = await button.evaluate((drawn) => {
      const area = getComputedStyle(drawn, "::before");
      return Math.min(parseFloat(area.width), parseFloat(area.height));
    });
    expect(hit).toBeGreaterThanOrEqual(44);
    await expect(button.locator("kbd")).toHaveCount(0);
    await button.tap();
    await expect(page.getByRole("main").getByText("This page is left out of the download")).toBeVisible();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
  });

  test("a date's control in the slip shows no key, and its calendar's days are a finger's size", async ({ page }) => {
    await open(page);
    await editTheDate(page);
    await page.getByRole("button", { name: "Date" }).tap();
    const day = page.getByRole("button", { name: /March 14, 2026/ });
    await expect(day).toBeVisible();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await expect(page.getByText(/Alt|⌥/)).toHaveCount(0);
    expect((await day.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  });

  test("a tap on a day the font can't write says why, and a second tap chooses it", async ({ page }) => {
    await open(page);
    // Page 2's line is in a subset of the file's own font, which drew only the digits it printed.
    const line = "Delivery begins 14 March 2026 and runs eighteen months.";
    await page.getByRole("button", { name: line }).focus();
    await page.keyboard.press("Enter");
    const field = page.getByRole("textbox", { name: `Change “${line}”` });
    await page.getByRole("button", { name: "Date" }).tap();
    const seventeenth = page.getByRole("button", { name: /March 17, 2026/ });
    await seventeenth.tap();
    await expect(page.locator("[data-slip]").getByText("no 7 in this font", { exact: true })).toBeVisible();
    await expect(field).toHaveValue(line);
    await seventeenth.tap();
    await expect(field).toHaveValue("Delivery begins 17 March 2026 and runs eighteen months.");
  });
});

test.describe("new text on a touch screen", () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });

  test("a double tap on bare paper opens new text there, and a single tap opens none", async ({ page }) => {
    await open(page);
    // The sheet, first: the layer its slips hang from, after it, has its shape too.
    const shape = page.getByRole("region", { name: "Page 1" }).locator("[style*='aspect-ratio']");
    const sheet = await shape.first().boundingBox();
    if (sheet === null) throw new Error("the first page is drawn");
    // Bare paper, under the page's last line.
    const [x, y] = [sheet.x + sheet.width * 0.3, sheet.y + sheet.height * 0.6];
    await page.touchscreen.tap(x, y);
    await expect(page.getByRole("textbox", { name: "New text" })).toHaveCount(0);
    await page.touchscreen.tap(x, y);
    const field = page.getByRole("textbox", { name: "New text" });
    await expect(field).toBeFocused();
    const shown = await field.boundingBox();
    expect(Math.abs((shown?.x ?? 0) - x)).toBeLessThan(2);
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

  test("a date's control in the slip shows its key on hover", async ({ page }) => {
    await open(page);
    // A click opens the field, as a mouse does: after a key, hover shows no tooltip, as React Aria has it.
    await page.getByRole("button", { name: DATED }).click();
    await page.getByRole("button", { name: "Date" }).hover();
    await expect(page.getByRole("tooltip")).toHaveText(/Alt ↓|⌥↓/);
  });
});

/** Open the field on the first page's line with a date in it, from the keyboard. */
async function editTheDate(page: Page) {
  await page.getByRole("button", { name: DATED }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("textbox", { name: `Change “${DATED}”` })).toBeFocused();
}
