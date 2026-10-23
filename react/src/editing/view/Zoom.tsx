// How large the pages show, from a slip that floats at the window's foot, clear of the bar's row of
// tools: in a step, out a step, and back to fitting the window, its share of the printed size said between.
import type { PressEvent } from "react-aria-components";
import { Group } from "react-aria-components";
import { Button } from "../../ui/Button";
import { shallowEqual } from "../store";
import { zoomBy, zoomToFit } from "../zoom";
import { useEditor, useEditorState } from "./context";
import styles from "./Zoom.module.css";

/** Where the window's middle is in the document, as shares of its height and of the pages' width. */
interface Place {
  readonly down: number;
  readonly across: number;
}

/** Where the middle of the window is now, the pages being `pages`. */
function placeOf(pages: Element | null): Place {
  const root = document.scrollingElement ?? document.documentElement;
  const down = (root.scrollTop + window.innerHeight / 2) / Math.max(root.scrollHeight, 1);
  const across = pages === null ? 0.5 : (pages.scrollLeft + pages.clientWidth / 2) / Math.max(pages.scrollWidth, 1);
  return { down, across };
}

/** Back to `place` once the pages are drawn at their new size, so what was in the middle stays there. */
function keepPlace(pages: Element | null, place: Place): void {
  requestAnimationFrame(() => {
    const root = document.scrollingElement ?? document.documentElement;
    root.scrollTop = place.down * root.scrollHeight - window.innerHeight / 2;
    if (pages !== null) pages.scrollLeft = place.across * pages.scrollWidth - pages.clientWidth / 2;
  });
}

/** The zoom's slip: out, the share shown (pressed, back to fitting the window), and in. */
export function ZoomControl() {
  const editor = useEditor();
  const zoom = useEditorState((state) => state.zoom, shallowEqual);
  const percent = Math.round(zoom.shown * 100);
  // Each press keeps the middle of the window where it was, as a viewer's zoom does.
  const zooming = (change: () => void) => (event: PressEvent) => {
    const pages = event.target.closest("main");
    const place = placeOf(pages);
    change();
    keepPlace(pages, place);
  };
  const pixelRatio = window.devicePixelRatio;
  return (
    <Group className={styles.zoom} aria-label="Zoom">
      <Button variant="quiet" aria-label="Zoom out" onPress={zooming(() => zoomBy(editor, -1, { pixelRatio }))}>
        <Sign />
      </Button>
      <Button
        variant="quiet"
        className={styles.share}
        aria-label={`Fit to the window, now ${percent}%`}
        onPress={zooming(() => zoomToFit(editor))}
      >
        {percent}%
      </Button>
      <Button variant="quiet" aria-label="Zoom in" onPress={zooming(() => zoomBy(editor, 1, { pixelRatio }))}>
        <Sign plus />
      </Button>
    </Group>
  );
}

/** Minus, or plus, drawn. The button's name says which. */
function Sign({ plus = false }: { plus?: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    >
      <path d={plus ? "M3 7h8M7 3v8" : "M3 7h8"} />
    </svg>
  );
}
