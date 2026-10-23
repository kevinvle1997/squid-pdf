// The core loop, end to end against the real API: drop, see every span marked, edit in
// place, the server's render settles in, undo puts it back, and Cmd+S downloads the file.
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { type Download, expect, type Locator, type Page } from "@playwright/test";
import type { Document } from "../src/api/types";
import { aProblem } from "../src/fixtures";
import { test } from "./policy";

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

/** Open the field on a span from the keyboard, as Enter does; `exact` once a margin note names its words too. */
async function startEdit(page: Page, { line = LINE, exact = false } = {}): Promise<Locator> {
  await page.getByRole("button", { name: line, exact }).focus();
  await page.keyboard.press("Enter");
  return page.getByRole("textbox", { name: `Change “${line}”` });
}

async function edit(page: Page, text: string) {
  const field = await startEdit(page);
  await field.fill(text);
  await field.press("Enter");
}

test("the landing takes a PDF anywhere, and nothing on it fails an accessibility check", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Drop a PDF here");
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

test("a span in a similar font says so as its edit starts, until its font is quieted", async ({ page }) => {
  await open(page);
  // At rest it says nothing: the bar's count speaks for the whole document.
  await page.mouse.move(0, 0);
  const heading = page.getByRole("button", { name: "SERVICES AGREEMENT" });
  await heading.hover();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await heading.focus();
  await page.keyboard.press("Enter");
  // On the page, beside the field: the live region says it too, for a screen reader.
  const note = page.getByRole("region", { name: "Page 1" }).getByText(/Edits here use Liberation Serif Bold/);
  await expect(note).toBeVisible();
  await page.getByRole("button", { name: "Don't show again for this font" }).click();
  await expect(note).toHaveCount(0);
  await expect(page.getByText("a similar font", { exact: true })).toBeVisible();
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

test("a reload loses nothing: the landing offers the document kept here, and it opens again with its change", async ({
  page,
}) => {
  await open(page);
  const changed = "This agreement is made on 2 April 2026 between";
  await edit(page, changed);
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  await page.reload();

  await page.getByRole("button", { name: "Open it again" }).click();
  await expect(page.getByRole("button", { name: changed, exact: true })).toBeVisible();
  await expect(page.getByText("1 change")).toBeVisible();
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
});

test("a span edited back to its own words is untouched: nothing is drawn over it", async ({ page }) => {
  await open(page);
  await edit(page, "This agreement is made on 2 April 2026 between");
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  const field = await startEdit(page, { line: "This agreement is made on 2 April 2026 between", exact: true });
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
  const unreachable = "Couldn't reach the server.";
  await expect(page.getByRole("paragraph").filter({ hasText: unreachable })).toBeVisible();
  await expect(page.getByRole("status")).toContainText(unreachable);
  // Until then the browser's own drawing of the edit stays up, over the original.
  await expect(page.locator("[data-preview]")).toHaveCount(1);

  await page.unroute("**/render");
  await context.setOffline(true);
  await context.setOffline(false);
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  await expect(page.locator("[data-preview]")).toHaveCount(0);
});

test("a page image the server refuses says why on the page, and comes back with the connection", async ({
  page,
  context,
}) => {
  const busy = "We can't work on files right now. Try again in a few minutes.";
  await page.route("**/pages/**", (route) =>
    route.fulfill({ status: 503, contentType: "application/problem+json", body: JSON.stringify(aProblem(503, busy)) }),
  );
  await open(page);
  const first = page.getByRole("region", { name: "Page 1" });
  await expect(first.getByText(busy)).toBeVisible();
  await expect(page.getByRole("status")).toContainText(busy);

  await page.unroute("**/pages/**");
  await context.setOffline(true);
  await context.setOffline(false);
  await expect(first.getByText(busy)).toHaveCount(0);
  await expect(first.locator("img").first()).toBeVisible();
});

test("a change put back from the margin leaves the page as it was", async ({ page }) => {
  await open(page);
  await edit(page, "This agreement is made on 2 April 2026 between");
  await page.getByRole("button", { name: /^Undo: “This agreement/ }).click();
  await expect(page.locator("img[data-strip]")).toHaveCount(0);
});

test("Cmd+S downloads the edited PDF under its own name, marked edited", async ({ page }) => {
  await open(page);
  await edit(page, "This agreement is made on 2 April 2026 between");
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  const downloading = page.waitForEvent("download");
  await page.keyboard.press("Control+s");
  const download = await downloading;
  expect(download.suggestedFilename()).toBe("sample, edited.pdf");
  const bytes = await readFile(await download.path());
  expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
  await expect(page.getByRole("paragraph").filter({ hasText: "Downloaded sample, edited.pdf." })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Downloaded sample, edited.pdf.");
});

test("Cmd+Delete redacts a line, and the file downloaded no longer has its words", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: LINE }).focus();
  await page.keyboard.press("ControlOrMeta+Backspace");
  await expect(page.getByRole("button", { name: `${LINE} (redacted)` })).toBeFocused();
  // The server drew it, its words gone from the strip as they'll be from the file.
  await expect(page.locator("img[data-strip]")).toHaveCount(1);

  const downloading = page.waitForEvent("download");
  await page.keyboard.press("ControlOrMeta+s");
  const bytes = await readFile(await (await downloading).path());
  // Read back as any upload is: the line's words aren't in the file's text at all.
  const reread = await page.evaluate(
    async (pdf) => {
      const body = new Uint8Array(pdf);
      const response = await fetch("/api/documents", {
        method: "POST",
        headers: { "content-type": "application/pdf" },
        body,
      });
      const doc = (await response.json()) as Document;
      return doc.spans.map((span) => span.text);
    },
    [...bytes],
  );
  expect(reread.length).toBeGreaterThan(0);
  expect(reread.join(" ")).not.toContain("This agreement is made");
});

test("Split here downloads two files, each with only its pages, and the edit in the one it's on", async ({ page }) => {
  await open(page);
  await edit(page, "This agreement is made on 9 June 2026 between");
  const downloads: Download[] = [];
  page.on("download", (download) => downloads.push(download));
  await page.getByRole("button", { name: "Split after page 1" }).click();
  await expect.poll(() => downloads.length).toBe(2);
  const [first, second] = downloads as [Download, Download];
  expect([first.suggestedFilename(), second.suggestedFilename()]).toEqual(["sample, page 1.pdf", "sample, page 2.pdf"]);
  // Each read back as any upload is: one page apiece, and the edit only where its page went.
  const reread = async (download: Download) => {
    const bytes = await readFile(await download.path());
    return page.evaluate(
      async (pdf) => {
        const response = await fetch("/api/documents", {
          method: "POST",
          headers: { "content-type": "application/pdf" },
          body: new Uint8Array(pdf),
        });
        const doc = (await response.json()) as Document;
        return { pages: doc.pages.length, text: doc.spans.map((span) => span.text).join(" ") };
      },
      [...bytes],
    );
  };
  const [before, after] = [await reread(first), await reread(second)];
  expect([before.pages, after.pages]).toEqual([1, 1]);
  expect(before.text).toContain("9 June 2026");
  expect(after.text).not.toContain("9 June 2026");
});

test("find counts a date on both pages, Change all changes both, and one undo puts both back", async ({ page }) => {
  await open(page);
  await page.keyboard.press("Control+f");
  await page.getByRole("textbox", { name: "Find" }).fill("14 March 2026");
  const bar = page.getByRole("search", { name: "Find and replace" });
  await expect(bar.getByText(/^2 places\b/)).toBeVisible();
  await page.getByRole("textbox", { name: "Change to" }).fill("15 March 2026");
  await bar.getByRole("button", { name: "Change all" }).click();
  await expect(page.locator("img[data-strip]")).toHaveCount(2);
  await expect(
    page.getByRole("button", { name: "Delivery begins 15 March 2026 and runs eighteen months.", exact: true }),
  ).toBeAttached();

  await page.getByRole("textbox", { name: "Find" }).press("Escape");
  await page.keyboard.press("Control+z");
  await expect(page.locator("img[data-strip]")).toHaveCount(0);
  await expect(page.getByRole("button", { name: LINE })).toBeVisible();
});

test("Cmd+S while still typing exports the edit being typed", async ({ page }) => {
  await open(page);
  await (await startEdit(page)).fill("This agreement is made on 3 May 2026 between");
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
  const field = await startEdit(page);
  await field.evaluate((input: HTMLInputElement) => input.setSelectionRange(input.value.length, input.value.length));
  // Each letter makes it longer; the number on screen ticks, what's said doesn't.
  await field.pressSequentially("xxx");
  await expect(page.getByRole("status")).toHaveText(
    "about 3% too long for its place: shorten it, or it runs into what's beside it; Up or Down chooses how it fits",
  );
  await field.fill("This agreement is made on 2 April 2026 between");

  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Wescott Analytics Ltd and Lindqvist & Rowe LLP." })).toBeFocused();
  const preview = page.locator("[data-preview]");
  expect(await preview.evaluate((drawn) => getComputedStyle(drawn).fontFamily)).toContain("preview Liberation Serif");
  release();
  await expect(page.locator("img[data-strip]")).toHaveCount(1);
  await expect(preview).toHaveCount(0);
});
