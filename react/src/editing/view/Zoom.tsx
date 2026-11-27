import type { PressEvent } from "react-aria-components";
import { Group } from "react-aria-components";
import { Button } from "../../ui/Button";
import { shallowEqual } from "../store";
import { zoomBy, zoomToFit } from "../zoom";
import { useEditor, useEditorState } from "./context";
import styles from "./Zoom.module.css";

interface Place {
  readonly down: number;
  readonly across: number;
}

function placeOf(pages: Element | null): Place {
  const root = document.scrollingElement ?? document.documentElement;
  const down = (root.scrollTop + window.innerHeight / 2) / Math.max(root.scrollHeight, 1);
  const across = pages === null ? 0.5 : (pages.scrollLeft + pages.clientWidth / 2) / Math.max(pages.scrollWidth, 1);
  return { down, across };
}

function keepPlace(pages: Element | null, place: Place): void {
  requestAnimationFrame(() => {
    const root = document.scrollingElement ?? document.documentElement;
    root.scrollTop = place.down * root.scrollHeight - window.innerHeight / 2;
    if (pages !== null) pages.scrollLeft = place.across * pages.scrollWidth - pages.clientWidth / 2;
  });
}

export function ZoomControl() {
  const editor = useEditor();
  const zoom = useEditorState((state) => state.zoom, shallowEqual);
  const percent = Math.round(zoom.shown * 100);
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
