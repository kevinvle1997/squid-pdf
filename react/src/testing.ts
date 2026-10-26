// What view tests read off the browser, or set it up as, here once a third test needs it: the
// browser's tests/helpers.py. Only tests import it (layers.test.ts); the API's shapes are fixtures.ts's.
import { expect, onTestFinished } from "vitest";
import { cdp } from "vitest/browser";

/** The colour `token` resolves to outside any sheet, read off a probe taken out again. */
export function colorOf(token: string): string {
  const probe = document.body.appendChild(document.createElement("span"));
  probe.style.color = `var(${token})`;
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

/** Make the browser a touch screen, its pointer coarse as a phone's, until the test ends. */
export async function emulateTouchScreen() {
  await cdp().send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
  onTestFinished(async () => {
    await cdp().send("Emulation.setTouchEmulationEnabled", { enabled: false });
  });
  expect(matchMedia("(pointer: coarse)").matches).toBe(true);
}
