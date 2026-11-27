import { useCallback } from "react";
import { Link } from "react-aria-components";
import { isShortcutPress, passShortcut } from "../commands";
import { type Editor, focusMoved } from "../editor";
import { goToPage } from "../pageList";
import { shownNumberOf } from "../project";
import { useEditor, useEditorState } from "./context";
import { sheetWidthOf } from "./geometry";
import styles from "./PageList.module.css";

export function PageList() {
  const order = useEditorState((state) => state.reading.order);
  const first = useEditorState((state) => state.doc.pages[state.reading.order[0] ?? 0]);
  const asked = useEditorState((state) => state.zoom.asked);
  const placed = { ...(first !== undefined && sheetWidthOf(first)), ...(asked !== null && { "--zoom": asked }) };
  return (
    <nav aria-label="Pages" className={styles.list} style={placed} data-zoomed={asked === null ? undefined : ""}>
      <div className={styles.ticks}>
        <ol>
          {order.map((page, index) => (
            <li key={page}>
              <Tick page={page} shownNumber={index + 1} />
            </li>
          ))}
        </ol>
        <Where />
      </div>
    </nav>
  );
}

function Tick({ page, shownNumber }: { page: number; shownNumber: number }) {
  const editor = useEditor();
  const current = useEditorState((state) => shownNumberOf(state.reading.order, state.pageInView) === shownNumber);
  const takeFocus = useFocusSent(editor, page);
  return (
    <Link
      ref={takeFocus}
      className={styles.tick}
      aria-label={`Page ${shownNumber}`}
      aria-current={current ? "page" : undefined}
      data-page={page}
      onPressStart={passShortcut}
      onPress={(event) => {
        if (!isShortcutPress(event)) goToPage(editor, page);
      }}
    />
  );
}

function useFocusSent(editor: Editor, page: number) {
  const sent = useEditorState(
    (state) => state.focusTo !== null && "tick" in state.focusTo && state.focusTo.tick === page,
  );
  return useCallback(
    (tick: HTMLAnchorElement | null) => {
      if (!sent || tick === null) return;
      // The list is held under the bar, in view: the page stays where it is.
      tick.focus({ preventScroll: true });
      focusMoved(editor);
    },
    [sent, editor],
  );
}

function Where() {
  const where = useEditorState(
    (state) => `${shownNumberOf(state.reading.order, state.pageInView)}/${state.reading.order.length}`,
  );
  return (
    <span className={styles.where} aria-hidden="true">
      {where}
    </span>
  );
}
