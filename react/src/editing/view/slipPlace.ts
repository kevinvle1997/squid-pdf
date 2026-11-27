import { type CSSProperties, useCallback, useState } from "react";
import { flushSync } from "react-dom";
import type { PageInfo, SpanInfo } from "../../api/types";
import { type Side, type SlipSize, slipPlaceOf, slipSideOf, uprightOf, type WindowRoom } from "./geometry";

// --s4, the gutter text keeps from the window's edge (design.md, Space and layout).
const GUTTER_PX = 16;

export function useSlipPlace(
  line: SpanInfo["bbox"],
  { page, endPt = line.x1 }: { page: PageInfo; endPt?: number },
): { slipRef: (element: HTMLElement | null) => (() => void) | undefined; style: CSSProperties } {
  const [measured, setMeasured] = useState<{ size: SlipSize; room: WindowRoom }>();
  // The side it's on, chosen as it's first measured: typing that grows it never moves it across its line.
  const [held, setHeld] = useState<Side>();
  const side = measured === undefined ? undefined : slipSideOf(line, page, { ...measured, endPt, held });
  if (side !== held) setHeld(side);
  const shownWidthPt = uprightOf(page).width;
  const slipRef = useCallback(
    (element: HTMLElement | null) => {
      const layer = element?.offsetParent;
      if (element == null || !(layer instanceof HTMLElement)) return;
      const scroller = scrollerOf(layer);
      let detached = false;
      const measure = () => {
        if (layer.clientWidth === 0) return;
        const ptPerPx = shownWidthPt / layer.clientWidth;
        // To the fraction of a pixel, as `offsetWidth` isn't: rounded down, it would wrap at the page's edge.
        const { width, height } = element.getBoundingClientRect();
        const size = { width: width * ptPerPx, height: height * ptPerPx };
        const room = roomOf(layer, { scroller, ptPerPx });
        // Placed before the frame is painted: a frame at its old place would show it grown over its line.
        flushSync(() => setMeasured({ size, room }));
        // Once it has arrived: a tap's own mouse events land after it's placed, and would press what scrolled under them.
        void arrivalOf(element).then(() => {
          if (!detached) bringIntoView(element, scroller);
        });
      };
      const resized = new ResizeObserver(measure);
      resized.observe(element);
      resized.observe(layer);
      if (scroller !== null) resized.observe(scroller);
      // A phone's keyboard shortens what the window shows, which resizes nothing observed.
      window.visualViewport?.addEventListener("resize", measure);
      return () => {
        detached = true;
        resized.disconnect();
        window.visualViewport?.removeEventListener("resize", measure);
      };
    },
    [shownWidthPt],
  );
  const style = slipPlaceOf(line, page, { ...measured, endPt, side });
  return { slipRef, style };
}

function arrivalOf(element: HTMLElement): Promise<unknown> {
  return Promise.allSettled(element.getAnimations().map((motion) => motion.finished));
}

function scrollerOf(element: HTMLElement): HTMLElement | null {
  for (let box = element.parentElement; box !== null; box = box.parentElement) {
    const { overflowX } = getComputedStyle(box);
    if (overflowX === "auto" || overflowX === "scroll") return box;
  }
  return null;
}

function roomOf(
  layer: HTMLElement,
  { scroller, ptPerPx }: { scroller: HTMLElement | null; ptPerPx: number },
): WindowRoom {
  const { left, right } = acrossOf(layer, scroller);
  const seen = seenOf(scroller);
  const shown = layer.getBoundingClientRect();
  const pt = (px: number) => px * ptPerPx;
  const zoom = seen.zoomControl;
  return {
    left: pt(left - GUTTER_PX),
    right: pt(right - GUTTER_PX),
    top: pt(shown.top - seen.top),
    bottom: pt(seen.foot - shown.bottom),
    pagesStart: pt(shown.top - seen.pagesStart),
    pagesEnd: pt(seen.pagesEnd - shown.bottom),
    zoomControl: zoom === null ? null : { left: pt(zoom.left - shown.left), bottom: pt(zoom.top - shown.bottom) },
  };
}

function acrossOf(layer: HTMLElement, scroller: HTMLElement | null): { left: number; right: number } {
  const windowRight = document.documentElement.clientWidth;
  const shown = layer.getBoundingClientRect();
  if (scroller === null) {
    const pageLeft = shown.left + window.scrollX;
    return { left: pageLeft, right: windowRight - pageLeft - layer.clientWidth };
  }
  const box = scroller.getBoundingClientRect();
  const boxLeft = Math.max(box.left + scroller.clientLeft, 0);
  const boxRight = Math.min(box.left + scroller.clientLeft + scroller.clientWidth, windowRight);
  const pageLeft = shown.left + scroller.scrollLeft;
  return { left: pageLeft - boxLeft, right: boxRight - pageLeft - layer.clientWidth };
}

interface Seen {
  readonly top: number;
  readonly foot: number;
  readonly pagesStart: number;
  readonly pagesEnd: number;
  readonly zoomControl: DOMRect | null;
}

function seenOf(scroller: HTMLElement | null): Seen {
  const visual = window.visualViewport;
  const windowTop = visual?.offsetTop ?? 0;
  const windowFoot = windowTop + (visual?.height ?? window.innerHeight);
  const pagesStart = scroller === null ? -Infinity : scroller.getBoundingClientRect().top + scroller.clientTop;
  const pagesEnd = scroller === null ? Infinity : pagesStart + scroller.clientHeight;
  return {
    top: Math.max(windowTop, stickyFootOf(scroller), pagesStart),
    foot: Math.min(windowFoot, pagesEnd),
    pagesStart,
    pagesEnd,
    zoomControl: zoomControlOf(scroller),
  };
}

function stickyFootOf(scroller: HTMLElement | null): number {
  const sticky = scroller?.previousElementSibling;
  if (!(sticky instanceof HTMLElement) || getComputedStyle(sticky).position !== "sticky") return 0;
  return Math.max(...[sticky, ...sticky.children].map((box) => box.getBoundingClientRect().bottom));
}

function zoomControlOf(scroller: HTMLElement | null): DOMRect | null {
  const last = scroller?.lastElementChild;
  if (!(last instanceof HTMLElement) || getComputedStyle(last).position !== "fixed") return null;
  return last.getBoundingClientRect();
}

function bringIntoView(slip: HTMLElement, scroller: HTMLElement | null): void {
  const shown = slip.getBoundingClientRect();
  const { top, foot, zoomControl: zoom } = seenOf(scroller);
  const overZoom = zoom !== null && shown.right > zoom.left && shown.left < zoom.right;
  const bottom = overZoom ? Math.min(foot, zoom.top) : foot;
  const under = Math.ceil(top - shown.top);
  if (under > 0) {
    window.scrollBy(0, -under);
    return;
  }
  const past = Math.min(Math.ceil(shown.bottom - bottom), Math.floor(shown.top - top));
  if (past > 0) window.scrollBy(0, past);
}
