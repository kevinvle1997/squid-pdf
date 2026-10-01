// The core loop, end to end against the real API: drop, see every span marked, edit in
// place, the server's render settles in, undo puts it back, and Cmd+S downloads the file.
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const SAMPLE = fileURLToPath(new URL("../../fixtures/sample.pdf", import.meta.url));
const LINE = "This agreement is made on 14 March 2026 between";

async function open(page: Page) {
  await page.goto("/");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose a PDF" }).click();
  await (await chooser).setFiles(SAMPLE);
  await expect(page.getByRole("region", { name: "Page 1" })).toBeVisible();
}

async function seriousProblems(page: Page) {
  const { violations } = await new AxeBuilder({ page }).analyze();
  return violations.filter((found) => found.impact === "serious" || found.impact === "critical");
}

async function edit(page: Page, text: string) {
  await page.getByRole("button", { name: LINE }).focus();
  await page.keyboard.press("Enter");
  const field = page.getByRole("textbox", { name: `Change “${LINE}”` });
  await field.fill(text);
  await field.press("Enter");
}

test("the landing is only a drop target, and nothing on it fails an accessibility check", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Drop a PDF anywhere to fix its words");
  expect(await seriousProblems(page)).toEqual([]);
});

test("an opened PDF shows its pages, with every span marked", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("region", { name: "Page 2" })).toBeAttached();
  const image = page.getByRole("region", { name: "Page 1" }).locator("img").first();
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByRole("button", { name: LINE })).toBeVisible();
  expect(await seriousProblems(page)).toEqual([]);
});

test("a span in a similar font says so before it's edited", async ({ page }) => {
  await open(page);
  // React Aria shows a hover note once it knows a pointer is in use: a real pointer moves
  // across the page first, where this test's hover would jump straight onto the span.
  await page.mouse.move(0, 0);
  await page.getByRole("button", { name: "SERVICES AGREEMENT" }).hover();
  await expect(page.getByRole("tooltip")).toContainText("Liberation Serif Bold");
});

test("an edit is drawn by the server, and undo puts the original back", async ({ page }) => {
  await open(page);
  await edit(page, "This agreement is made on 2 April 2026 between");
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  await expect(page.getByText("1 change")).toBeVisible();
  // Focus is back on the span, but its note would cover what was just typed.
  await expect(
    page.getByRole("button", { name: "This agreement is made on 2 April 2026 between", exact: true }),
  ).toBeFocused();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Undo: “This agreement is made on 2 April 2026/ })).toBeVisible();

  await page.keyboard.press("Control+z");
  await expect(page.locator("img[data-strip]")).toHaveCount(0);
  await expect(page.getByText("1 change")).toHaveCount(0);

  await page.keyboard.press("Control+Shift+z");
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
});

test("a span edited back to its own words is untouched: nothing is drawn over it", async ({ page }) => {
  await open(page);
  await edit(page, "This agreement is made on 2 April 2026 between");
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  await page.getByRole("button", { name: "This agreement is made on 2 April 2026 between", exact: true }).focus();
  await page.keyboard.press("Enter");
  const field = page.getByRole("textbox", { name: "Change “This agreement is made on 2 April 2026 between”" });
  await field.fill(LINE);
  await field.press("Enter");
  // Drawn again, the line would come back in the substitute while the page says nothing changed.
  await expect(page.locator("img[data-strip]")).toHaveCount(0);
  await expect(page.getByText("1 change")).toHaveCount(0);
});

test("a render lost to the network is asked for again when the connection comes back", async ({ page, context }) => {
  await open(page);
  await page.route("**/render", (route) => route.abort("internetdisconnected"));
  await edit(page, "This agreement is made on 2 April 2026 between");
  await expect(page.getByText("Couldn't reach the server.", { exact: false })).toBeVisible();
  // Until then the browser's own drawing of the edit stays up, over the original.
  await expect(page.locator("[data-preview]")).toHaveCount(1);

  await page.unroute("**/render");
  await context.setOffline(true);
  await context.setOffline(false);
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  await expect(page.locator("[data-preview]")).toHaveCount(0);
});

test("a change put back from the margin leaves the page as it was", async ({ page }) => {
  await open(page);
  await edit(page, "This agreement is made on 2 April 2026 between");
  await page.getByRole("button", { name: /^Undo: “This agreement/ }).click();
  await expect(page.locator("img[data-strip]")).toHaveCount(0);
});

test("Cmd+S downloads the edited PDF under its own name", async ({ page }) => {
  await open(page);
  await edit(page, "This agreement is made on 2 April 2026 between");
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  const downloading = page.waitForEvent("download");
  await page.keyboard.press("Control+s");
  const download = await downloading;
  expect(download.suggestedFilename()).toBe("sample.pdf");
  const bytes = await readFile(await download.path());
  expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
  await expect(page.getByRole("paragraph").filter({ hasText: "Downloaded sample.pdf." })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Downloaded sample.pdf.");
});

test("Cmd+S while still typing exports the edit being typed", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: LINE }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("textbox", { name: `Change “${LINE}”` }).fill("This agreement is made on 3 May 2026 between");
  const sent = page.waitForRequest((request) => request.url().endsWith("/export"));
  const downloading = page.waitForEvent("download");
  await page.keyboard.press("Control+s");
  expect((await sent).postData()).toContain("3 May 2026");
  await downloading;
});

test("typing previews in a similar font, says a trouble once, and Tab goes on to the next span", async ({ page }) => {
  await open(page);
  // Held, so the browser's preview stays up to be looked at.
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/render", async (route) => {
    await held;
    await route.continue();
  });
  await page.getByRole("button", { name: LINE }).focus();
  await page.keyboard.press("Enter");
  const field = page.getByRole("textbox", { name: `Change “${LINE}”` });
  await field.evaluate((input: HTMLInputElement) => input.setSelectionRange(input.value.length, input.value.length));
  // Each letter makes it longer; the number on screen ticks, what's said doesn't.
  await field.pressSequentially("xxx");
  await expect(page.getByRole("status")).toHaveText("5.5 pt too long");
  await field.fill("This agreement is made on 2 April 2026 between");

  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Wescott Analytics Ltd and Lindqvist & Rowe LLP." })).toBeFocused();
  const preview = page.locator("[data-preview]");
  expect(await preview.evaluate((drawn) => getComputedStyle(drawn).fontFamily)).toContain("preview Liberation Serif");
  release();
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  await expect(preview).toHaveCount(0);
});
