// The core loop, end to end against the real API: drop, see every span marked, edit in
// place, the server's render settles in, undo puts it back, and Cmd+S downloads the file.
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { type Download, expect, type FileChooser, type Locator, type Page } from "@playwright/test";
import type { Document } from "../src/api/types";
import { aProblem } from "../src/fixtures";
import { test } from "./policy";

const SAMPLE = fileURLToPath(new URL("../../fixtures/sample.pdf", import.meta.url));
const LINE = "This agreement is made on 14 March 2026 between";

/** A file to open: a path, or its bytes and name. */
type Upload = Parameters<FileChooser["setFiles"]>[0];

async function open(page: Page, file: Upload = SAMPLE) {
  await page.goto("/");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose a PDF" }).click();
  await (await chooser).setFiles(file);
  await expect(page.getByRole("region", { name: "Page 1" })).toBeVisible();
}

/**
 * Three pages, each one line in Helvetica naming it: one more page than the sample, so a page
 * deleted still leaves two to split. Written by hand, each object's place counted for the xref.
 */
function threePages(): Upload {
  const lines = ["First page", "Second page", "Third page"];
  const kids = lines.map((_, index) => `${4 + 2 * index} 0 R`).join(" ");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    `<< /Type /Pages /Kids [${kids}] /Count ${lines.length} >>`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ...lines.flatMap((line, index) => {
      const content = `BT /F1 24 Tf 72 720 Td (${line}) Tj ET`;
      return [
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents ${5 + 2 * index} 0 R >>`,
        `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
      ];
    }),
  ];
  let pdf = "%PDF-1.4\n";
  const places = objects.map((object, index) => {
    const place = pdf.length;
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
    return place;
  });
  const xref = pdf.length;
  const rows = places.map((place) => `${String(place).padStart(10, "0")} 00000 n \n`).join("");
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${rows}`;
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return { name: "three.pdf", mimeType: "application/pdf", buffer: Buffer.from(pdf, "latin1") };
}

/** A PDF read back as any upload is. */
async function reread(page: Page, pdf: Uint8Array): Promise<Document> {
  return page.evaluate(
    async (sent) => {
      const response = await fetch("/api/documents", {
        method: "POST",
        headers: { "content-type": "application/pdf" },
        body: new Uint8Array(sent),
      });
      return (await response.json()) as Document;
    },
    [...pdf],
  );
}

/** A download's bytes. */
async function bytesOf(download: Download): Promise<Uint8Array> {
  return readFile(await download.path());
}

/** A document's words, a span's after another's. */
function wordsOf(doc: Document): string {
  return doc.spans.map((span) => span.text).join(" ");
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
  await expect(page.getByRole("heading", { level: 1 })).toHaveAccessibleName("Drop a PDF here");
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

test("a span in a similar font says so as its edit starts, until its font's warning is muted", async ({ page }) => {
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

test("a reload loses nothing: the landing offers the document persisted here, and it opens again with its change", async ({
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

test("a date changed through the calendar downloads in the file's own font, and nothing fails an accessibility check", async ({
  page,
}) => {
  await open(page);
  // Page 2's line is in a subset of the file's own font, which drew only the digits it printed.
  const line = "Delivery begins 14 March 2026 and runs eighteen months.";
  const field = await startEdit(page, { line });
  await field.press("Alt+ArrowDown");
  await expect(page.getByRole("button", { name: /March 14, 2026/ })).toBeFocused();
  // Before the pick, a day it can't write says why.
  await page.getByRole("button", { name: /March 17, 2026/ }).hover();
  await expect(page.locator("[data-slip]").getByText("no 7 in this font", { exact: true })).toBeVisible();
  expect(await seriousProblems(page)).toEqual([]);

  await page.getByRole("button", { name: /March 16, 2026/ }).click();
  const changed = "Delivery begins 16 March 2026 and runs eighteen months.";
  await expect(field).toHaveValue(changed);
  await expect(field).toBeFocused();
  await field.press("Enter");
  await expect(page.locator("img[data-strip]")).toHaveCount(1);

  const downloading = page.waitForEvent("download");
  await page.keyboard.press("ControlOrMeta+s");
  const [before, after] = [
    await reread(page, await readFile(SAMPLE)),
    await reread(page, await bytesOf(await downloading)),
  ];
  const original = before.spans.find((span) => span.text === line);
  const edited = after.spans.find((span) => span.text === changed);
  // Drawn again from the file's own program, which names itself as PostScript does: NimbusRoman-Regular.
  const letters = (name = "") => name.replace(/^[A-Z]{6}\+/, "").replace(/[^A-Za-z]/g, "");
  expect(letters(edited?.font)).toBe(letters(original?.font));
  expect(edited?.fidelity).toBe("exact");
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
  // Read back as any upload is: the line's words aren't in the file's text at all.
  const redacted = await reread(page, await bytesOf(await downloading));
  expect(redacted.spans.length).toBeGreaterThan(0);
  expect(wordsOf(redacted)).not.toContain("This agreement is made");
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
  // One page apiece, and the edit only where its page went.
  const [before, after] = [await reread(page, await bytesOf(first)), await reread(page, await bytesOf(second))];
  expect([before.pages.length, after.pages.length]).toEqual([1, 1]);
  expect(wordsOf(before)).toContain("9 June 2026");
  expect(wordsOf(after)).not.toContain("9 June 2026");
});

test("a page deleted in the editor is missing from the download, and its slip passes an accessibility check", async ({
  page,
}) => {
  await open(page);
  await page.getByRole("button", { name: "Delete page 1", exact: true }).click();
  await expect(page.getByRole("main").getByText("This page is left out of the download")).toBeVisible();
  await expect(page.getByRole("button", { name: "Put back" })).toBeFocused();
  expect(await seriousProblems(page)).toEqual([]);

  const downloading = page.waitForEvent("download");
  await page.keyboard.press("ControlOrMeta+s");
  const kept = await reread(page, await bytesOf(await downloading));
  expect(kept.pages.length).toBe(1);
  expect(wordsOf(kept)).toContain("Schedule 1");
  expect(wordsOf(kept)).not.toContain("SERVICES AGREEMENT");
});

test("Split here after a page is deleted downloads the pages as shown, the deleted page in neither", async ({
  page,
}) => {
  await open(page, threePages());
  await page.getByRole("button", { name: "Delete page 2", exact: true }).click();
  const downloads: Download[] = [];
  page.on("download", (download) => downloads.push(download));
  await page.getByRole("button", { name: "Split after page 1" }).click();
  await expect.poll(() => downloads.length).toBe(2);
  const [first, second] = downloads as [Download, Download];
  expect([first.suggestedFilename(), second.suggestedFilename()]).toEqual(["three, page 1.pdf", "three, page 3.pdf"]);
  const [before, after] = [await reread(page, await bytesOf(first)), await reread(page, await bytesOf(second))];
  expect([before.pages.length, after.pages.length]).toEqual([1, 1]);
  expect([wordsOf(before), wordsOf(after)]).toEqual(["First page", "Third page"]);
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
  // Each letter makes it longer; the number on screen ticks, what's announced doesn't.
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
