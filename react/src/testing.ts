import { expect, onTestFinished } from "vitest";
import { cdp } from "vitest/browser";

export function colorOf(token: string): string {
  const probe = document.body.appendChild(document.createElement("span"));
  probe.style.color = `var(${token})`;
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

export async function emulateTouchScreen() {
  await cdp().send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
  onTestFinished(async () => {
    await cdp().send("Emulation.setTouchEmulationEnabled", { enabled: false });
  });
  expect(matchMedia("(pointer: coarse)").matches).toBe(true);
}

export function middleOf(box: DOMRect): { x: number; y: number } {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

export async function animationsDone(): Promise<void> {
  await Promise.all(document.getAnimations().map((animation) => animation.finished));
}

export async function tapAt(at: { x: number; y: number }) {
  await cdp().send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [at] });
  await cdp().send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}
