// The performance probe: typing, the render settling and scrolling, on a long contract in
// the built app, with the CPU slowed to a mid-range laptop's and, once the file is open, the
// network to a desk's. It logs what it measures and asserts nothing: numbers are compared by
// hand, before and after a change, and kept out of this repository (CLAUDE.md: measure before
// optimising). Run it with `npm run perf`.
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { type CDPSession, expect, type Page } from "@playwright/test";
import { test } from "./policy";

const LINE = "This agreement is made on 14 March 2026 between";
const PAGES = 30; // "tens of pages": the size the editor is built for
const SLOWDOWN = 4; // DevTools' "mid-tier mobile"; a laptop on battery is close
// A fast network, as the budgets promise: broadband at a desk, not a train, where the settle
// would fail by construction. The API is local, so every byte of the delay is these.
const LATENCY_MS = 40; // a round trip to a server a country away
const DOWN_MBIT = 10; // broadband's slower end
const UP_MBIT = 5;
const BYTES_PER_MBIT = 1_000_000 / 8;
const TYPED = "abcdefghijklmnopqrst";
const REPO = fileURLToPath(new URL("../..", import.meta.url));

test.skip(!process.env.PERF, "the probe runs on its own: npm run perf");

interface Probe {
  count: boolean; // walk the tree on each commit: costs time, so not while timing
  prime: () => void; // take the tree as it stands, for the first commit counted to compare against
  commits: number;
  rendered: number[]; // components each commit rendered
  latencies: number[]; // keydown to the frame after it
  longTasks: number[];
  presses: number[]; // when each key or pointer went down
  requests: Asked[]; // each render and export asked for
}

interface Asked {
  kind: string; // "render" or "export"
  start: number;
  end: number; // 0 while it's out
  aborted: boolean; // dropped by the app before its answer was read
}

declare global {
  interface Window {
    probe: Probe;
  }
}

/** Installed before the app loads: React reports every commit to a DevTools hook, in production too. */
function instrument() {
  type Fiber = { tag: number; flags: number; child: Fiber | null; sibling: Fiber | null };
  // Function, class, forwardRef, memo and simple memo components.
  const COMPONENTS = new Set([0, 1, 11, 14, 15]);
  const PERFORMED_WORK = 1;
  let before = new WeakSet<Fiber>();
  let last: { current: Fiber } | null = null;
  const walk = (root: { current: Fiber }, visit: (fiber: Fiber) => void) => {
    const stack = [root.current];
    for (let fiber = stack.pop(); fiber !== undefined; fiber = stack.pop()) {
      visit(fiber);
      if (fiber.sibling !== null) stack.push(fiber.sibling);
      if (fiber.child !== null) stack.push(fiber.child);
    }
  };
  const prime = () => {
    const now = new WeakSet<Fiber>();
    if (last !== null) walk(last, (fiber) => now.add(fiber));
    before = now;
  };
  const probe: Probe = {
    count: false,
    prime,
    commits: 0,
    rendered: [],
    latencies: [],
    longTasks: [],
    presses: [],
    requests: [],
  };
  window.probe = probe;
  Object.assign(window, {
    __REACT_DEVTOOLS_GLOBAL_HOOK__: {
      supportsFiber: true,
      inject: () => 1,
      checkDCE: () => undefined,
      onCommitFiberUnmount: () => undefined,
      onPostCommitFiberRoot: () => undefined,
      onCommitFiberRoot(_renderer: number, root: { current: Fiber }) {
        probe.commits++;
        last = root;
        if (!probe.count) return;
        // A fiber that rendered in this commit is new to the tree and did work; one React
        // skipped is the very object that was there last time.
        const now = new WeakSet<Fiber>();
        let rendered = 0;
        walk(root, (fiber) => {
          now.add(fiber);
          if (COMPONENTS.has(fiber.tag) && fiber.flags & PERFORMED_WORK && !before.has(fiber)) rendered++;
        });
        before = now;
        probe.rendered.push(rendered);
      },
    },
  });
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) probe.longTasks.push(entry.duration);
  }).observe({ type: "longtask" });
  // Each render and export, from its request to the last byte of its answer.
  const fetched = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const kind = (input instanceof Request ? input.url : String(input)).match(/\/(render|export)$/)?.[1];
    if (kind === undefined) return fetched(input, init);
    const asked: Asked = { kind, start: performance.now(), end: 0, aborted: false };
    probe.requests.push(asked);
    try {
      const response = await fetched(input, init);
      await response.clone().arrayBuffer();
      return response;
    } catch (error) {
      asked.aborted = true;
      throw error;
    } finally {
      asked.end = performance.now();
    }
  };
  window.addEventListener("pointerdown", () => probe.presses.push(performance.now()), true);
  // To the frame after the key: after the next animation frame, once its task has run.
  window.addEventListener(
    "keydown",
    () => {
      const start = performance.now();
      probe.presses.push(start);
      requestAnimationFrame(() => {
        const channel = new MessageChannel();
        channel.port1.onmessage = () => probe.latencies.push(performance.now() - start);
        channel.port2.postMessage(null);
      });
    },
    true,
  );
}

function summary(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b);
  const at = (share: number) => sorted[Math.min(sorted.length - 1, Math.floor(share * sorted.length))] ?? 0;
  const round = (value: number) => Math.round(value * 10) / 10;
  return { n: sorted.length, median: round(at(0.5)), p95: round(at(0.95)), max: round(sorted.at(-1) ?? 0) };
}

/** Where Chrome's main thread spent its time, in ms: script, layout, style. */
async function busy(cdp: CDPSession) {
  const { metrics } = await cdp.send("Performance.getMetrics");
  const of = (name: string) => (metrics.find((metric) => metric.name === name)?.value ?? 0) * 1000;
  return { script: of("ScriptDuration"), layout: of("LayoutDuration"), style: of("RecalcStyleDuration") };
}

function spent(before: Awaited<ReturnType<typeof busy>>, after: Awaited<ReturnType<typeof busy>>) {
  return {
    scriptMs: Math.round(after.script - before.script),
    layoutMs: Math.round(after.layout - before.layout),
    styleMs: Math.round(after.style - before.style),
  };
}

async function reset(page: Page, count: boolean) {
  await page.evaluate((counting) => {
    if (counting) window.probe.prime();
    Object.assign(window.probe, {
      count: counting,
      commits: 0,
      rendered: [],
      latencies: [],
      longTasks: [],
      presses: [],
      requests: [],
    });
  }, count);
}

/** From the next press, of a pointer or a key, to `selector` matching in the page, in ms. */
function timeTo(page: Page, selector: string) {
  return page.evaluate(
    (wanted) =>
      new Promise<number>((resolve) => {
        let start = 0;
        const pressed = () => {
          start = performance.now();
          window.removeEventListener("pointerdown", pressed, true);
          window.removeEventListener("keydown", pressed, true);
        };
        window.addEventListener("pointerdown", pressed, true);
        window.addEventListener("keydown", pressed, true);
        const watch = new MutationObserver(() => {
          if (start === 0 || document.querySelector(wanted) === null) return;
          watch.disconnect();
          resolve(performance.now() - start);
        });
        watch.observe(document.body, { subtree: true, childList: true });
      }),
    selector,
  );
}

/** Once every request asked for since the last reset is answered or dropped: how many, and when, from the last press. */
async function answered(page: Page) {
  await expect
    .poll(
      () => page.evaluate(() => window.probe.requests.length > 0 && window.probe.requests.every((r) => r.end > 0)),
      {
        timeout: 60_000,
      },
    )
    .toBe(true);
  return page.evaluate(() => {
    const { requests, presses } = window.probe;
    const kept = requests.filter((asked) => !asked.aborted);
    const pressed = presses.at(-1) ?? 0;
    const since = (at: number) => Math.round(at - pressed);
    return {
      asked: requests.length,
      dropped: requests.length - kept.length,
      lastPressToAskedMs: since(Math.max(...requests.map((asked) => asked.start))),
      lastPressToAnswerMs: since(Math.max(...kept.map((asked) => asked.end))),
      answerMs: kept.map((asked) => Math.round(asked.end - asked.start)),
    };
  });
}

async function openField(page: Page) {
  await page.getByRole("button", { name: LINE }).focus();
  await page.keyboard.press("Enter");
  const field = page.getByRole("textbox", { name: `Change “${LINE}”` });
  await field.press("End");
  return field;
}

/** The long contract, open in the built app, and its marks at rest; then the CPU and the network slowed. */
async function openContract(page: Page) {
  const pdf = join(mkdtempSync(join(tmpdir(), "squidpdf-perf-")), "dense.pdf");
  execFileSync("uv", ["run", "squidpdf", "fixture", pdf, "--pages", String(PAGES)], { cwd: REPO });

  await page.addInitScript(instrument);
  await page.goto("/");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose a PDF" }).click();
  await (await chooser).setFiles(pdf);
  await expect(page.getByRole("button", { name: LINE })).toBeVisible();
  await page.waitForLoadState("networkidle");
  const marks = await page.locator("main button").count();

  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: SLOWDOWN });
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: LATENCY_MS,
    downloadThroughput: DOWN_MBIT * BYTES_PER_MBIT,
    uploadThroughput: UP_MBIT * BYTES_PER_MBIT,
  });
  await cdp.send("Performance.enable");
  return { cdp, marks };
}

test("typing, the render settling and scrolling on a long contract", async ({ page }) => {
  test.setTimeout(180_000);
  const { cdp, marks } = await openContract(page);

  // Timed with nothing else running in the page.
  let field = await openField(page);
  await reset(page, false);
  const typingFrom = await busy(cdp);
  await field.pressSequentially(TYPED, { delay: 120 });
  const typingBusy = spent(typingFrom, await busy(cdp));
  const typing = await page.evaluate(() => ({ ...window.probe }));
  await field.press("Escape");

  // Counted apart: walking the tree slows the commit it counts.
  field = await openField(page);
  await reset(page, true);
  await field.pressSequentially(TYPED, { delay: 120 });
  const counted = await page.evaluate(() => ({ ...window.probe }));

  // Enter to the server's strip in place of the preview.
  const settled = page.evaluate(
    () =>
      new Promise<number>((resolve) => {
        const start = performance.now();
        const watch = new MutationObserver(() => {
          if (document.querySelector("img[data-strip]") === null) return;
          watch.disconnect();
          resolve(performance.now() - start);
        });
        watch.observe(document.body, { subtree: true, childList: true });
      }),
  );
  await field.press("Enter");
  const settle = await settled;

  // Top to bottom, a wheel notch at a time.
  await page.mouse.move(400, 400);
  await reset(page, false);
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  const scrollFrom = await busy(cdp);
  const started = Date.now();
  for (let y = 0; y < height; y += 600) {
    await page.mouse.wheel(0, 600);
    await page.waitForTimeout(50);
  }
  const scroll = await page.evaluate(() => ({ ...window.probe }));
  const scrollBusy = spent(scrollFrom, await busy(cdp));

  const report = {
    pages: PAGES,
    slowdown: SLOWDOWN,
    network: { latencyMs: LATENCY_MS, downMbit: DOWN_MBIT, upMbit: UP_MBIT },
    marksMountedAtRest: marks,
    keystrokeToFrameMs: summary(typing.latencies),
    longTasksWhileTypingMs: summary(typing.longTasks),
    whileTyping: typingBusy,
    commitsPerKeystroke: counted.commits / TYPED.length,
    componentsRenderedPerKeystroke: summary(counted.rendered),
    enterToStripMs: Math.round(settle),
    scroll: {
      wallMs: Date.now() - started,
      longTasksMs: summary(scroll.longTasks),
      longTaskTotalMs: Math.round(scroll.longTasks.reduce((sum, value) => sum + value, 0)),
      commits: scroll.commits,
      ...scrollBusy,
    },
  };
  console.log(`perf ${JSON.stringify(report, null, 2)}`);
  await test.info().attach("perf.json", { body: JSON.stringify(report, null, 2), contentType: "application/json" });
});

test("change all, a burst of undos and export on a long contract", async ({ page }) => {
  test.setTimeout(180_000);
  await openContract(page);
  const firstPageStrip = 'section[aria-label="Page 1"] img[data-strip]';

  // A heading on every page: one render draws them all, the first page's strip with the rest.
  await page.keyboard.press("ControlOrMeta+f");
  await page.getByRole("textbox", { name: "Find" }).fill("Schedule");
  await page.getByRole("textbox", { name: "Change to" }).fill("Annex");
  const changeAll = page.getByRole("button", { name: "Change all" });
  await expect(changeAll).toBeEnabled();
  await reset(page, false);
  const changedToStrip = timeTo(page, firstPageStrip);
  await changeAll.click();
  const allPages = { pressToFirstPageStripMs: Math.round(await changedToStrip), ...(await answered(page)) };
  await page.getByRole("button", { name: /^Close/ }).click();

  // One line changed alone, for the first page's strip when a render draws only it.
  let field = await openField(page);
  await field.pressSequentially(" too");
  await reset(page, false);
  await field.press("Enter");
  const onePage = await answered(page);

  // Four more lines, then undo one, then three in a burst, as fast as a held key.
  const lines = page.locator('section[aria-label="Page 1"] [data-span]');
  for (const line of [3, 5, 7, 9]) {
    await lines.nth(line).focus();
    await page.keyboard.press("Enter");
    field = page.getByRole("textbox", { name: /^Change “/ });
    await field.press("End");
    await field.pressSequentially(" too");
    await field.press("Enter");
  }
  await answered(page);
  await reset(page, false);
  await page.keyboard.press("ControlOrMeta+z");
  const oneUndo = await answered(page);
  await reset(page, false);
  for (let press = 0; press < 3; press++) {
    await page.keyboard.press("ControlOrMeta+z");
    await page.waitForTimeout(80);
  }
  const undoBurst = await answered(page);

  // Export: the request, and the press to the download.
  await reset(page, false);
  const downloaded = page.waitForEvent("download");
  const pressed = Date.now();
  await page.getByRole("button", { name: /^Export/ }).click();
  await downloaded;
  const pressToDownloadMs = Date.now() - pressed;
  const exported = await answered(page);

  const report = {
    pages: PAGES,
    slowdown: SLOWDOWN,
    network: { latencyMs: LATENCY_MS, downMbit: DOWN_MBIT, upMbit: UP_MBIT },
    changeAllOnEveryPage: allPages,
    oneLineOnOnePage: onePage,
    oneUndo,
    undoBurstOfThree: undoBurst,
    export: { pressToDownloadMs, ...exported },
  };
  console.log(`perf ${JSON.stringify(report, null, 2)}`);
  await test
    .info()
    .attach("perf-flows.json", { body: JSON.stringify(report, null, 2), contentType: "application/json" });
});
