import { Link } from "react-aria-components";
import { isShortcutPress, passShortcut } from "../commands";
import { goToPage } from "../pageList";
import { shownNumberOf } from "../project";
import { useEditor, useEditorState } from "./context";
import { sheetWidthOf } from "./geometry";
import styles from "./PageList.module.css";

/**
 * The page list, beside the first page and held under the bar as the document scrolls; across the top on a phone.
 * A tick for each page the download has, in order, each going to its page; and which page is being read.
 */
export function PageList() {
  const order = useEditorState((state) => state.reading.order);
  const first = useEditorState((state) => state.doc.pages[state.reading.order[0] ?? 0]);
  // Placed as the first page is, zoomed or not.
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

/** One page's tick, the page being read's drawn longer: drawn alone, since that changes with each page scrolled past. */
function Tick({ page, shownNumber }: { page: number; shownNumber: number }) {
  const editor = useEditor();
  const current = useEditorState((state) => shownNumberOf(state.reading.order, state.pageInView) === shownNumber);
  return (
    <Link
      className={styles.tick}
      aria-label={`Page ${shownNumber}`}
      aria-current={current ? "page" : undefined}
      data-page={page}
      // Cmd+Enter goes on to the window's shortcuts: on a page with no text, it opens new text there.
      onPressStart={passShortcut}
      onPress={(event) => {
        if (!isShortcutPress(event)) goToPage(editor, page);
      }}
    />
  );
}

/** "2/4": the page being read, of the pages the download has. The bar says it in words, so it's not said twice. */
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
