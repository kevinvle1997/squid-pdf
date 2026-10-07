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
  const probe: Probe = { count: false, prime, commits: 0, rendered: [], latencies: [], longTasks: [] };
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
  // To the frame after the key: after the next animation frame, once its task has run.
  window.addEventListener(
    "keydown",
    () => {
      const start = performance.now();
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
    Object.assign(window.probe, { count: counting, commits: 0, rendered: [], latencies: [], longTasks: [] });
  }, count);
}

async function openField(page: Page) {
  await page.getByRole("button", { name: LINE }).focus();
  await page.keyboard.press("Enter");
  const field = page.getByRole("textbox", { name: `Change “${LINE}”` });
  await field.press("End");
  return field;
}

test("typing, the render settling and scrolling on a long contract", async ({ page }) => {
  test.setTimeout(180_000);
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
