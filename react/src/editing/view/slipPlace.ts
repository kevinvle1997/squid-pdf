// Where a slip on a page sits once its size is known: the field's chip and the redaction prompt alike.
import { type CSSProperties, useCallback, useState } from "react";
import { flushSync } from "react-dom";
import type { PageInfo, SpanInfo } from "../../api/types";
import { type Side, type SlipSize, slipPlaceOf, slipSideOf, uprightOf, type WindowRoom } from "./geometry";

// --s4, the gutter text keeps from the window's edge (design.md, Space and layout).
const GUTTER_PX = 16;

/**
 * A slip about `line`, drawn to `endPt`, placed as `slipPlaceOf` says, and again as it or its page is shown
 * another size: an open calendar is wider than a verdict. While `holdSide`, it keeps the side of its line it
 * was on, so growing never moves it from under the finger. The ref goes on the slip, the style with it.
 */
export function useSlipPlace(
  line: SpanInfo["bbox"],
  { page, endPt = line.x1, holdSide = false }: { page: PageInfo; endPt?: number; holdSide?: boolean },
): { slipRef: (element: HTMLElement | null) => (() => void) | undefined; style: CSSProperties } {
  const [measured, setMeasured] = useState<{ size: SlipSize; room: WindowRoom }>();
  const size = measured?.size;
  // The side it's on as it starts holding, worked out from its size before it grows.
  const [held, setHeld] = useState<Side>();
  if (holdSide && held === undefined && size !== undefined) setHeld(slipSideOf(line, page, { size, endPt }));
  if (!holdSide && held !== undefined) setHeld(undefined);
  const shownWidthPt = uprightOf(page).width;
  const slipRef = useCallback(
    (element: HTMLElement | null) => {
      const layer = element?.offsetParent;
      if (element == null || !(layer instanceof HTMLElement)) return;
      const scroller = scrollerOf(layer);
      const resized = new ResizeObserver(() => {
        // Not laid out yet: there's nothing to measure by.
        if (layer.clientWidth === 0) return;
        const ptPerPx = shownWidthPt / layer.clientWidth;
        // To the fraction of a pixel, as `offsetWidth` isn't: rounded down, it would wrap at the page's edge.
        const { width, height } = element.getBoundingClientRect();
        const { left, right } = acrossOf(layer, scroller);
        const room = { left: (left - GUTTER_PX) * ptPerPx, right: (right - GUTTER_PX) * ptPerPx };
        // Placed before the frame is painted: a frame at its old place would show it grown over its line.
        flushSync(() => setMeasured({ size: { width: width * ptPerPx, height: height * ptPerPx }, room }));
      });
      resized.observe(element);
      resized.observe(layer);
      // What the page scrolls in is shown wider or narrower with the window, the page perhaps not.
      if (scroller !== null) resized.observe(scroller);
      return () => resized.disconnect();
    },
    [shownWidthPt],
  );
  const style = slipPlaceOf(line, page, { size, endPt, side: held, room: measured?.room });
  return { slipRef, style };
}

/** The box `element` scrolls sideways in, the nearest that does: none for the window. */
function scrollerOf(element: HTMLElement): HTMLElement | null {
  for (let box = element.parentElement; box !== null; box = box.parentElement) {
    const { overflowX } = getComputedStyle(box);
    if (overflowX === "auto" || overflowX === "scroll") return box;
  }
  return null;
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
