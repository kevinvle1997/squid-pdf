// Where a slip on a page sits once its size is known: the field's chip and the redaction prompt alike.
import { type CSSProperties, useCallback, useState } from "react";
import { flushSync } from "react-dom";
import type { PageInfo, SpanInfo } from "../../api/types";
import { type Side, type SlipSize, slipPlaceOf, slipSideOf, uprightOf, type WindowRoom } from "./geometry";

// --s4, the gutter text keeps from the window's edge (design.md, Space and layout).
const GUTTER_PX = 16;

/**
 * A slip about `line`, drawn to `endPt`, placed as `slipPlaceOf` says, and again as it, its page or the window is
 * shown another size, as an open calendar or a phone's keyboard makes them. The ref goes on the slip.
 *
 * It keeps the side of its line it opened on, growing away from the line, but where the pages would cut it there.
 * Whatever the window doesn't show whole of it is scrolled into view, its top first, which holds the verdict.
 */
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
        // Not laid out yet: there's nothing to measure by.
        if (layer.clientWidth === 0) return;
        const ptPerPx = shownWidthPt / layer.clientWidth;
        // To the fraction of a pixel, as `offsetWidth` isn't: rounded down, it would wrap at the page's edge.
        const { width, height } = element.getBoundingClientRect();
        const size = { width: width * ptPerPx, height: height * ptPerPx };
        const room = roomOf(layer, { scroller, ptPerPx });
        // Placed before the frame is painted: a frame at its old place would show it grown over its line.
        flushSync(() => setMeasured({ size, room }));
        // Once it has arrived: a tap's own mouse events land after it's placed, and scrolled under them they'd
        // press what moved there.
        void arrivalOf(element).then(() => {
          if (!detached) bringIntoView(element, scroller);
        });
      };
      const resized = new ResizeObserver(measure);
      resized.observe(element);
      resized.observe(layer);
      // What the page scrolls in is shown wider or narrower with the window, the page perhaps not.
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

/** When `element`'s motion as it arrives ends: at once, for one already there. */
function arrivalOf(element: HTMLElement): Promise<unknown> {
  return Promise.allSettled(element.getAnimations().map((motion) => motion.finished));
}

/** The box `element` scrolls sideways in, the nearest that does, the pages: none for the window. */
function scrollerOf(element: HTMLElement): HTMLElement | null {
  for (let box = element.parentElement; box !== null; box = box.parentElement) {
    const { overflowX } = getComputedStyle(box);
    if (overflowX === "auto" || overflowX === "scroll") return box;
  }
  return null;
}

/** How far the window reaches past `layer`'s edges, as `WindowRoom` says, at `ptPerPx` of the page's points a pixel. */
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

/**
 * How far what `layer` scrolls in shows past its left and right edges, in pixels, and within the window: the page
 * as it lies there unscrolled, so a scroll, such as the one that brings a focused day into view, changes nothing.
 * A slip past the edge it's kept to scrolls it, as focus goes in.
 */
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

/** What the window shows of the pages, down it, in pixels as it's scrolled now. */
interface Seen {
  readonly top: number; // under what's held over the pages
  readonly foot: number; // the window's foot, as a phone's keyboard leaves it (`visualViewport`), or the pages' end
  readonly pagesStart: number;
  readonly pagesEnd: number;
  readonly zoomControl: DOMRect | null;
}

/** What the window shows of the pages that `scroller` holds, down it: no end to them for none. */
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

/** Where what's held at the top over the pages ends, down the window: the bar's rows, and the lines hung under them. */
function stickyFootOf(scroller: HTMLElement | null): number {
  const sticky = scroller?.previousElementSibling;
  if (!(sticky instanceof HTMLElement) || getComputedStyle(sticky).position !== "sticky") return 0;
  // The lines under the bar hang from it, past its own box.
  return Math.max(...[sticky, ...sticky.children].map((box) => box.getBoundingClientRect().bottom));
}

/** The zoom control, the pages' last child, fixed over the window's foot: none where it isn't. */
function zoomControlOf(scroller: HTMLElement | null): DOMRect | null {
  const last = scroller?.lastElementChild;
  if (!(last instanceof HTMLElement) || getComputedStyle(last).position !== "fixed") return null;
  return last.getBoundingClientRect();
}

/**
 * Scrolls the window as little as brings `slip` whole into view, under what's held over the pages and over the
 * window's foot or the zoom control, or, taller than that, its top.
 */
function bringIntoView(slip: HTMLElement, scroller: HTMLElement | null): void {
  const shown = slip.getBoundingClientRect();
  const { top, foot, zoomControl: zoom } = seenOf(scroller);
  const overZoom = zoom !== null && shown.right > zoom.left && shown.left < zoom.right;
  const bottom = overZoom ? Math.min(foot, zoom.top) : foot;
  // In whole pixels, as the window scrolls: enough to show what's hidden, never so far its top goes under.
  const under = Math.ceil(top - shown.top);
  if (under > 0) {
    window.scrollBy(0, -under);
    return;
  }
  const past = Math.min(Math.ceil(shown.bottom - bottom), Math.floor(shown.top - top));
  if (past > 0) window.scrollBy(0, past);
}
