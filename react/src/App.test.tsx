import { beforeEach, expect, onTestFinished, test, vi } from "vitest";
import { render } from "vitest-browser-react";
import "./styles/tokens.css";
import "./styles/base.css";
import { userEvent } from "vitest/browser";
import { App } from "./App";
import { account, askForLink, ProblemError, signIn, signOut, terms, upload } from "./api/client";
import type { Render, Terms } from "./api/types";
import { SOMETHING_WENT_WRONG } from "./bugs";
import {
  forgetOrSayWhy,
  forgetPersisted,
  persistedDocument,
  persistFile,
  persistState,
} from "./documents/localPersistence";
import { EMPTY_HISTORY, historyReducer } from "./editing/history";
import { aDoc, aFont, anAccount, aProblem, aSpan, TERMS } from "./fixtures";
import { animationsDone, emulateTouchScreen } from "./testing";

vi.mock(import("./api/client"), async (original) => ({
  ...(await original()),
  upload: vi.fn(),
  renderStrips: vi.fn(() => new Promise<Render>(() => undefined)),
  stillThere: vi.fn(async () => true),
  pageImage: vi.fn(async () => undefined),
  terms: vi.fn(async () => null),
  account: vi.fn(),
  askForLink: vi.fn(),
  signIn: vi.fn(),
  signOut: vi.fn(),
}));
// The browser's real storage, but a delete can be held under way.
vi.mock(import("./documents/localPersistence"), async (original) => {
  const module = await original();
  return { ...module, forgetOrSayWhy: vi.fn(module.forgetOrSayWhy) };
});

beforeEach(async () => {
  vi.mocked(upload).mockReset();
  // Reset, so the calls a test reads are its own.
  vi.mocked(terms).mockReset().mockResolvedValue(null);
  // A server that keeps no accounts, as a self-hosted copy: the landing as it always was.
  vi.mocked(account)
    .mockReset()
    .mockResolvedValue(anAccount({ offered: false }));
  vi.mocked(askForLink).mockReset();
  vi.mocked(signIn).mockReset();
  vi.mocked(signOut).mockReset();
  // The browser's real storage, one test at a time: each starts with nothing persisted.
  await forgetPersisted();
});

// The server keeps it an hour from now, as it says on every read.
const PERSISTED_DOC = aDoc({
  id: "persisted",
  spans: [aSpan({ id: "a" })],
  fonts: [aFont("Times-Roman")],
  expires_at: new Date(Date.now() + 3_600_000).toISOString(),
});

/** Persist `contract.pdf` here as an editor would, its one change "is a", until `until`. */
async function persistedHere(until: number) {
  const history = historyReducer(EMPTY_HISTORY, {
    kind: "add",
    edits: [{ kind: "replace", span_id: "a", text: "is a" }],
  });
  await persistFile(new File(["%PDF-"], "contract.pdf", { type: "application/pdf" }));
  await persistState({ doc: PERSISTED_DOC, work: { history, fonts: [] }, until });
}

/** A PDF chosen on the landing, as its file picker hands one over. */
function choose(container: HTMLElement, name: string) {
  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  const files = new DataTransfer();
  files.items.add(new File(["%PDF-"], name, { type: "application/pdf" }));
  input.files = files.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

test("a bug while opening is said, and another file can be chosen", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.mocked(upload).mockRejectedValue(new TypeError("request.response is null"));
  const screen = await render(<App />);
  choose(screen.container, "contract.pdf");
  // The landing's words for a bug, not the crash screen's, which would also start "Something went wrong".
  await expect.element(screen.getByText(SOMETHING_WENT_WRONG)).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toBeEnabled();
});

test("a PDF dropped on the editor opens in place of the one open, as the landing opens one", async () => {
  const fonts = [aFont("Times-Roman")];
  vi.mocked(upload)
    .mockResolvedValueOnce(aDoc({ id: "first", spans: [aSpan({ id: "a" })], fonts }))
    .mockResolvedValueOnce(aDoc({ id: "second", spans: [aSpan({ id: "b" })], fonts }));
  const screen = await render(<App />);
  choose(screen.container, "contract.pdf");
  await expect.element(screen.getByText("contract.pdf")).toBeVisible();

  const files = new DataTransfer();
  files.items.add(new File(["%PDF-"], "invoice.pdf", { type: "application/pdf" }));
  // The editor listens from an effect, which can run after its file's name shows: a drag over
  // the page is taken once it does, since both are listened for together.
  await expect
    .poll(() => {
      const over = new DragEvent("dragover", { dataTransfer: files, bubbles: true, cancelable: true });
      document.body.dispatchEvent(over);
      return over.defaultPrevented;
    })
    .toBe(true);
  const drop = new DragEvent("drop", { dataTransfer: files, bubbles: true, cancelable: true });
  document.body.dispatchEvent(drop);
  // Never the browser's: it would open the file in this tab.
  expect(drop.defaultPrevented).toBe(true);
  await expect.element(screen.getByText("invoice.pdf")).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "was b" })).toBeInTheDocument();
});

test("a change is persisted in this browser as it's made, so the landing offers it after a reload", async () => {
  vi.mocked(upload).mockResolvedValueOnce(PERSISTED_DOC);
  const first = await render(<App />);
  choose(first.container, "contract.pdf");
  const mark = first.getByRole("button", { name: "was a" });
  await expect.element(mark).toBeInTheDocument();
  mark.element().focus();
  await userEvent.keyboard("{Enter}");
  await first.getByRole("textbox").fill("is a");
  await userEvent.keyboard("{Enter}");
  await expect.poll(async () => (await persistedDocument())?.file.name).toBe("contract.pdf");

  await first.unmount();
  const again = await render(<App />);
  await expect
    .element(again.getByText("Your changes to contract.pdf are kept in this browser until", { exact: false }))
    .toBeVisible();
});

test("a document persisted here opens again with its changes, without sending the file again", async () => {
  await persistedHere(Date.now() + 60_000);
  const screen = await render(<App />);
  await screen.getByRole("button", { name: "Open it again" }).click();
  await expect.element(screen.getByRole("button", { name: "is a" })).toBeInTheDocument();
  expect(upload).not.toHaveBeenCalled();
});

test("a document persisted before the server's sentences were renamed comes back with them under their new name", async () => {
  await persistedHere(Date.now() + 60_000);
  const { sentences, ...rest } = PERSISTED_DOC;
  // The shape a browser stored it in before, which no type names now.
  const before = { ...rest, copy: sentences } as unknown as typeof PERSISTED_DOC;
  await persistState({ doc: before, work: (await persistedDocument())?.work, until: Date.now() + 60_000 });
  expect((await persistedDocument())?.doc.sentences).toEqual(PERSISTED_DOC.sentences);
});

test("Delete it now forgets the document persisted here, file and all", async () => {
  await persistedHere(Date.now() + 60_000);
  const screen = await render(<App />);
  await screen.getByRole("button", { name: "Delete it now" }).click();
  await expect.element(screen.getByRole("button", { name: "Open it again" })).not.toBeInTheDocument();
  await expect.poll(() => persistedDocument()).toBeNull();
});

/** The browser's storage throwing `error` as anything persisted here is deleted, until the test ends. */
function deleteThrows(error: Error) {
  const throwing = vi.spyOn(IDBObjectStore.prototype, "clear").mockImplementation(() => {
    throw error;
  });
  onTestFinished(() => throwing.mockRestore());
  return throwing;
}

test("Delete it now refused by the browser's storage says so, keeps the offer, and can be pressed again", async () => {
  await persistedHere(Date.now() + 60_000);
  const screen = await render(<App />);
  const refusing = deleteThrows(new DOMException("Refused", "UnknownError"));
  await screen.getByRole("button", { name: "Delete it now" }).click();
  const refusal =
    "This browser wouldn't delete your changes to contract.pdf. Try again, or clear this site's data in the browser's settings.";
  await expect.element(screen.getByRole("status")).toHaveTextContent(refusal);
  await expect.element(screen.getByRole("button", { name: "Open it again" })).toBeVisible();
  refusing.mockRestore();
  await screen.getByRole("button", { name: "Delete it now" }).click();
  await expect.element(screen.getByRole("button", { name: "Open it again" })).not.toBeInTheDocument();
  await expect.element(screen.getByRole("status")).not.toHaveTextContent(refusal);
  await expect.poll(() => persistedDocument()).toBeNull();
});

test("a bug while deleting is said as a bug, not as the browser refusing", async () => {
  const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
  await persistedHere(Date.now() + 60_000);
  const screen = await render(<App />);
  deleteThrows(new TypeError("store is undefined"));
  await screen.getByRole("button", { name: "Delete it now" }).click();
  await expect.element(screen.getByRole("status")).toHaveTextContent(SOMETHING_WENT_WRONG);
  expect(logged).toHaveBeenCalledOnce();
});

test("Open it again waits while a delete is under way, so the delete can't take the work from the editor", async () => {
  await persistedHere(Date.now() + 60_000);
  vi.mocked(forgetOrSayWhy).mockReturnValueOnce(new Promise(() => undefined));
  const screen = await render(<App />);
  await screen.getByRole("button", { name: "Delete it now" }).click();
  await screen.getByRole("button", { name: "Open it again" }).click();
  // Opened, the offer would go at once, in the same update.
  await expect.element(screen.getByRole("button", { name: "Open it again" })).toBeVisible();
  await expect.poll(() => persistedDocument()).not.toBeNull();
});

test("one past the server's hour isn't offered, and is forgotten", async () => {
  await persistedHere(Date.now() - 1);
  const screen = await render(<App />);
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toBeVisible();
  await expect.poll(() => persistedDocument()).toBeNull();
  await expect.element(screen.getByRole("button", { name: "Open it again" })).not.toBeInTheDocument();
});

/** The app opened at `path`, as a link to it opens it, until the test ends. */
function openedAt(path: string) {
  const before = window.location.pathname;
  window.history.replaceState(null, "", path);
  onTestFinished(() => window.history.replaceState(null, "", before));
}

/** The server's answer to the ask for the terms, held until the test gives it, or fails it. */
function termsHeld() {
  let resolve: (answer: Terms | null) => void = () => undefined;
  let reject: (failure: unknown) => void = () => undefined;
  vi.mocked(terms).mockReturnValue(
    new Promise((given, refused) => {
      resolve = given;
      reject = refused;
    }),
  );
  return { resolve, reject };
}

test("the landing links the service's terms beside its code's link, where its deployment has them", async () => {
  vi.mocked(terms).mockResolvedValue(TERMS);
  const screen = await render(<App />);
  const link = screen.getByRole("link", { name: "Terms of use" });
  await expect.element(link).toHaveAttribute("href", "/terms");
  const code = screen.getByRole("link", { name: "read the code that does it" });
  expect(link.element().parentElement).toBe(code.element().parentElement);
});

test("a deployment with no terms, as a self-hosted copy, links none", async () => {
  const answer = termsHeld();
  const screen = await render(<App />);
  answer.resolve(null);
  await vi.mocked(terms).mock.results[0]?.value;
  // Drawn again inside React's act, which draws the answer first, so the link can't be still to come.
  await screen.rerender(<App />);
  await expect.element(screen.getByRole("link", { name: "read the code that does it" })).toBeVisible();
  await expect.element(screen.getByRole("link", { name: "Terms of use" })).not.toBeInTheDocument();
});

test("a landing that can't learn whether there are terms links none, as a self-hosted copy's", async () => {
  vi.mocked(terms).mockRejectedValue(new ProblemError(aProblem(0, "Couldn't reach the server.")));
  const screen = await render(<App />);
  await vi.mocked(terms).mock.results[0]?.value.catch(() => undefined);
  await screen.rerender(<App />);
  const code = screen.getByRole("link", { name: "read the code that does it" });
  // The sentence ends where it always has: nothing kept for a link that isn't there.
  expect(code.element().parentElement?.textContent?.endsWith("read the code that does it.")).toBe(true);
});

test("on a touch screen, the terms' link is a touch's size below its words, never over the line above", async () => {
  await emulateTouchScreen();
  vi.mocked(terms).mockResolvedValue(TERMS);
  const screen = await render(<App />);
  const link = screen.getByRole("link", { name: "Terms of use" });
  await expect.element(link).toBeVisible();
  const box = link.element().getBoundingClientRect();
  const middle = box.left + box.width / 2;
  expect(document.elementFromPoint(middle, box.top + box.height / 2 + 20)).toBe(link.element());
  // Its words' own line, and none above: the line before keeps its own words to tap.
  expect(document.elementFromPoint(middle, box.top - 4)).not.toBe(link.element());
  expect(document.elementFromPoint(middle, box.top - 12)).not.toBe(link.element());
});

test("the terms' link waits for the interface's font, so a stand-in's wrapping never jumps back as it comes", async () => {
  let loaded: (faces: FontFace[]) => void = () => undefined;
  const font = new Promise<FontFace[]>((resolve) => {
    loaded = resolve;
  });
  const loading = vi.spyOn(document.fonts, "load").mockReturnValue(font);
  onTestFinished(() => loading.mockRestore());
  vi.mocked(terms).mockResolvedValue(TERMS);
  const screen = await render(<App />);
  await vi.mocked(terms).mock.results[0]?.value;
  await screen.rerender(<App />);
  await expect.element(screen.getByRole("link", { name: "Terms of use" })).not.toBeInTheDocument();
  loaded([]);
  await expect.element(screen.getByRole("link", { name: "Terms of use" })).toBeVisible();
});

/** The landing's sentence that links the code: its height, a line's, and the gap under it to the facts. */
function sentenceAndGap(container: HTMLElement) {
  const code = [...container.querySelectorAll("a")].find((link) => link.textContent === "read the code that does it");
  const sentence = code?.parentElement as HTMLElement;
  const facts = container.querySelector("ul")?.getBoundingClientRect();
  const box = sentence.getBoundingClientRect();
  return {
    height: box.height,
    line: Number.parseFloat(getComputedStyle(sentence).lineHeight),
    gap: (facts?.top ?? 0) - box.bottom,
  };
}

test.each(["a mouse", "a touch screen"])(
  "with %s, the terms' link adds at most a line to the sentence, as tall as its others, and leaves the gap under it",
  async (pointer) => {
    if (pointer === "a touch screen") await emulateTouchScreen();
    const without = await render(<App />);
    await vi.mocked(terms).mock.results[0]?.value;
    await without.rerender(<App />);
    const plain = sentenceAndGap(without.container);
    await without.unmount();
    vi.mocked(terms).mockResolvedValue(TERMS);
    const linked = await render(<App />);
    await expect.element(linked.getByRole("link", { name: "Terms of use" })).toBeVisible();
    const withTerms = sentenceAndGap(linked.container);
    expect(withTerms.gap).toBe(plain.gap);
    const added = withTerms.height - plain.height;
    expect([0, plain.line].some((line) => Math.abs(added - line) < 0.5)).toBe(true);
  },
);

test("the landing settles within the layout shift budget as the terms' link comes", async () => {
  const answer = termsHeld();
  const screen = await render(<App />);
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toBeVisible();
  const shifts: number[] = [];
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) shifts.push((entry as PerformanceEntry & { value: number }).value);
  });
  observer.observe({ type: "layout-shift" });
  answer.resolve(TERMS);
  await expect.element(screen.getByRole("link", { name: "Terms of use" })).toBeVisible();
  await animationsDone();
  observer.disconnect();
  expect(shifts.reduce((sum, shift) => sum + shift, 0)).toBeLessThan(0.1);
});

test("the terms' own page says them: the title, when they last changed, each paragraph led in bold", async () => {
  openedAt("/terms");
  vi.mocked(terms).mockResolvedValue(TERMS);
  const screen = await render(<App />);
  await expect.element(screen.getByRole("heading", { level: 1 })).toHaveAccessibleName("Example terms");
  await expect.element(screen.getByText("Last changed 1 January 2000")).toBeVisible();
  const leads = [...screen.container.querySelectorAll("strong")].map((lead) => lead.textContent);
  expect(leads).toEqual(["First lead.", "Second lead."]);
  await expect
    .element(screen.getByRole("link", { name: "someone@example.com" }))
    .toHaveAttribute("href", "mailto:someone@example.com");
  expect(document.title).toBe("Example terms · squid-pdf");
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).not.toBeInTheDocument();
});

test("the terms' page leads home at its top and its foot, and is found with a trailing slash", async () => {
  openedAt("/terms/");
  vi.mocked(terms).mockResolvedValue(TERMS);
  const screen = await render(<App />);
  await expect.element(screen.getByRole("link", { name: "squid-pdf", exact: true })).toHaveAttribute("href", "/");
  await expect.element(screen.getByRole("link", { name: "Back to squid-pdf" })).toHaveAttribute("href", "/");
});

test("the terms' page shows its heading at once, and past a moment says it's reading them", async () => {
  openedAt("/terms");
  termsHeld();
  const screen = await render(<App />);
  await expect.element(screen.getByRole("heading", { level: 1, name: "Terms of use" })).toBeVisible();
  expect(document.title).toBe("Terms of use · squid-pdf");
  await expect.element(screen.getByRole("status")).toHaveTextContent("Reading the terms");
});

test("the terms' page, in a deployment with none, is the landing, as any other path is", async () => {
  openedAt("/terms");
  const screen = await render(<App />);
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toBeVisible();
});

test("the terms' page says why it can't read them in the status that was waiting for them, and offers Reload", async () => {
  openedAt("/terms");
  const answer = termsHeld();
  const screen = await render(<App />);
  const waiting = screen.getByRole("status");
  await expect.element(waiting).toBeInTheDocument();
  const region = waiting.element();
  answer.reject(new ProblemError(aProblem(0, "Couldn't reach the server.")));
  await expect.element(waiting).toHaveTextContent("Couldn't reach the server.");
  // The same region, so a screen reader hears the words as they come.
  expect(waiting.element()).toBe(region);
  await expect.element(screen.getByRole("button", { name: "Reload" })).toBeVisible();
});

const INVITED = { email: "friend@example.com", is_unlimited: false };
const LINK_SENT = "If friend@example.com is invited, a link is on its way.";

test("open to invited people only, a browser not signed in is asked to, and told where its link went", async () => {
  vi.mocked(account).mockResolvedValue(anAccount({ invite_only: true }));
  vi.mocked(askForLink).mockResolvedValue({ sentence: LINK_SENT });
  const screen = await render(<App />);
  await expect.element(screen.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect.element(screen.getByText("open to invited people for now", { exact: false })).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).not.toBeInTheDocument();
  await userEvent.fill(screen.getByRole("textbox", { name: "Email" }), "friend@example.com");
  await userEvent.click(screen.getByRole("button", { name: "Email me a link" }));
  await expect.element(screen.getByRole("status")).toHaveTextContent(LINK_SENT);
  expect(askForLink).toHaveBeenCalledExactlyOnceWith("friend@example.com");
});

test("a sign-in link's page trades its token, then is the landing, signed in, with the token gone from the address", async () => {
  window.history.replaceState(null, "", "/sign-in#the-token");
  onTestFinished(() => window.history.replaceState(null, "", "/"));
  vi.mocked(signIn).mockResolvedValue(anAccount({ invite_only: true, signed_in: INVITED }));
  const screen = await render(<App />);
  await expect.element(screen.getByText("Signed in as friend@example.com")).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toBeVisible();
  expect(signIn).toHaveBeenCalledExactlyOnceWith("the-token");
  expect(`${window.location.pathname}${window.location.hash}`).toBe("/");
});

test("a sign-in link past its time says so, open to invited people only, beside the way to ask for another", async () => {
  window.history.replaceState(null, "", "/sign-in#old-token");
  onTestFinished(() => window.history.replaceState(null, "", "/"));
  vi.mocked(signIn).mockRejectedValue(new ProblemError(aProblem(400, "This sign-in link has run out.")));
  vi.mocked(account).mockResolvedValue(anAccount({ invite_only: true }));
  const screen = await render(<App />);
  await expect.element(screen.getByRole("status")).toHaveTextContent("This sign-in link has run out.");
  await expect.element(screen.getByRole("button", { name: "Email me a link" })).toBeVisible();
});

test("a sign-in link past its time, where anyone may open a document, says so on the landing", async () => {
  window.history.replaceState(null, "", "/sign-in#old-token");
  onTestFinished(() => window.history.replaceState(null, "", "/"));
  vi.mocked(signIn).mockRejectedValue(new ProblemError(aProblem(400, "This sign-in link has run out.")));
  vi.mocked(account).mockResolvedValue(anAccount());
  const screen = await render(<App />);
  await expect.element(screen.getByText("This sign-in link has run out.")).toBeVisible();
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toBeVisible();
});

test("Sign out on the landing signs this browser out, and the landing no longer names it", async () => {
  vi.mocked(account).mockResolvedValue(anAccount({ signed_in: INVITED }));
  vi.mocked(signOut).mockResolvedValue(anAccount());
  const screen = await render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
  await expect.element(screen.getByText("Signed in as", { exact: false })).not.toBeInTheDocument();
  // The button pressed has gone: focus is on the sheet's one action, not lost to the page.
  await expect.element(screen.getByRole("button", { name: "Choose a PDF" })).toHaveFocus();
  expect(signOut).toHaveBeenCalledOnce();
});
