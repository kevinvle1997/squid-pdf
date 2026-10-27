import { useCallback, useId } from "react";
import { Button as AriaButton } from "react-aria-components";
import type { PageInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { NameTip } from "../../ui/Tooltip";
import { isShortcutPress, passShortcut } from "../commands";
import { focusMoved } from "../editor";
import { deletePage, leftOutSentence, putPageBack } from "../pageDeletion";
import { useEditor, useEditorState } from "./context";
import styles from "./DeletePage.module.css";
import { sheetWidthOf } from "./geometry";

/** A ref for page `page`'s own control, its delete button or its Put back: it takes focus as it's drawn when focus is sent there. */
function useFocusSent(page: number) {
  const editor = useEditor();
  const sent = useEditorState((state) => {
    const { focusTo } = state;
    return focusTo !== null && "page" in focusTo && focusTo.page === page ? focusTo : null;
  });
  return useCallback(
    (button: HTMLButtonElement | null) => {
      if (sent === null || button === null) return;
      // Brought into view as asked, not as focus would: a page gone to shows from its top.
      button.focus({ preventScroll: true });
      button.scrollIntoView({ block: sent.block, inline: "nearest" });
      focusMoved(editor);
    },
    [sent, editor],
  );
}

/**
 * Leave page `page` out of the download: one quiet button on the mat by the sheet's top corner, always shown,
 * named for its `shownNumber`. The last page left can't go: a download needs a page.
 */
export function DeletePage({ page, shownNumber }: { page: number; shownNumber: number }) {
  const editor = useEditor();
  const last = useEditorState((state) => state.reading.order.length === 1);
  const takeFocus = useFocusSent(page);
  const name = `Delete page ${shownNumber}`;
  return (
    <div className={styles.spot}>
      <NameTip name={name}>
        <AriaButton
          ref={takeFocus}
          className={styles.button}
          aria-label={name}
          isDisabled={last}
          // Cmd+Enter goes on to the window's shortcuts: on a page with no text, it opens new text there.
          onPressStart={passShortcut}
          onPress={(event) => {
            if (!isShortcutPress(event)) deletePage(editor, page);
          }}
        >
          <TrashCan />
        </AriaButton>
      </NameTip>
    </div>
  );
}

/** Page `page`, deleted: collapsed in its place to a slip the sheet's width, left out of the download, with Put back. */
export function DeletedPage({ page, info }: { page: number; info: PageInfo }) {
  const editor = useEditor();
  const sentence = useEditorState((state) => leftOutSentence(state.doc));
  const takeFocus = useFocusSent(page);
  const id = useId();
  return (
    <div className={styles.deleted} style={sheetWidthOf(info)} data-page={page}>
      <div className={styles.slip}>
        <p id={id} className={styles.said}>
          {sentence}
        </p>
        <Button ref={takeFocus} aria-describedby={id} onPress={() => putPageBack(editor, page)}>
          Put back
        </Button>
      </div>
    </div>
  );
}

/** A trash can: a lid and its handle, a can narrowing to its foot, two ribs. Drawn at its own size, so the stroke stays 1.5px. */
function TrashCan() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2.5 4h11M6.25 4V2.75A.75.75 0 0 1 7 2h2a.75.75 0 0 1 .75.75V4M3.75 4l.7 9.1a1.5 1.5 0 0 0 1.5 1.4h4.1a1.5 1.5 0 0 0 1.5-1.4L12.25 4M6.75 7v4.5M9.25 7v4.5" />
    </svg>
  );
}
