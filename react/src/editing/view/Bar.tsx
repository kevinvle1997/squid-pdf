// The bar over the document: its name and pages, what's changed and how much of it won't match, and
// the tools and the one action, Export.
import { useCallback } from "react";
import { QUIET_MS } from "../../documents/constants";
import { Button } from "../../ui/Button";
import { classes } from "../../ui/classes";
import { Warn } from "../../ui/Warn";
import { Wordmark } from "../../ui/Wordmark";
import { changedCount, type UnlikeAtRest, warnedCount } from "../editor";
import { exportAhead, exportIntended, exportNow, exportUnintended } from "../export";
import { openFind } from "../find";
import { shallowEqual } from "../store";
import { counted, formOf } from "../words";
import styles from "./Bar.module.css";
import { useEditor, useEditorState } from "./context";
import { Fonts } from "./Fonts";
import { COMMAND } from "./keys";

/** The bar, held at the top. */
export function Bar() {
  const editor = useEditor();
  const pages = useEditorState((state) => state.doc.pages.length);
  const changed = useEditorState(changedCount);
  const warned = useEditorState(warnedCount, shallowEqual);
  const atRest = useEditorState((state) => state.layout.unlikeAtRest);
  const exporting = useEditorState((state) => state.exporting);
  // The document opened again, or couldn't: said in the bar's status, beside the counts.
  const reopen = useEditorState((state) => state.notices.reopen);
  return (
    <header className={styles.bar}>
      <Wordmark />
      <span className={styles.file}>
        <span className={styles.name}>{editor.file.name}</span>
        <span className={styles.meta}>{counted(pages, { one: "page", other: "pages" })}</span>
      </span>
      {/* What's changed and the buttons, together: beside the file while they fit, under it when they don't. */}
      <span className={styles.end}>
        {reopen !== null && (
          <span className={styles.reopen}>{reopen.tone === "warn" ? <Warn>{reopen.text}</Warn> : reopen.text}</span>
        )}
        {changed === 0 && (
          // Beside the count before any change, it gives way to it where the bar is narrow.
          <span className={classes(styles.hint, atRest.count > 0 && styles.besideCount)}>
            <span className={styles.click}>Double-click text to change it</span>
            <span className={styles.tap}>Double-tap text to change it</span>
          </span>
        )}
        <span className={styles.status}>
          {changed === 0 ? <AtRest {...atRest} /> : <Changed changed={changed} {...warned} />}
        </span>
        {exporting && (
          // Hidden for the quiet spell by CSS, as opening a file is, so a quick export shows nothing.
          <span className={styles.exporting} style={{ animationDelay: `${QUIET_MS}ms` }}>
            Exporting
          </span>
        )}
        <span className={styles.actions}>
          <FindButton />
          <Fonts />
          <Button
            variant="primary"
            onPress={() => void exportNow(editor)}
            // On the way to pressing it, the export starts, for the press to take.
            onHoverStart={() => exportIntended(editor)}
            onHoverEnd={() => exportUnintended(editor)}
            onFocus={() => exportIntended(editor)}
            onBlur={() => exportUnintended(editor)}
            onPressStart={() => exportAhead(editor)}
            isDisabled={exporting}
          >
            Export <kbd>{COMMAND}S</kbd>
          </Button>
        </span>
      </span>
    </header>
  );
}

/** The changes, and how many of them won't match, the count warned: every one of them, or so many of them. */
function Changed({ changed, count, label }: { changed: number; count: number; label: string }) {
  const changes = formOf(changed, { one: "change", other: "changes" });
  // Each matches the text around it.
  if (count === 0) return `${changed} ${changes}`;
  // None does: the changes' own count is the warning.
  if (count === changed) {
    return (
      <>
        <Warn>{changed}</Warn> {changes} {label}
      </>
    );
  }
  return (
    <>
      {changed} {changes}, <Warn>{count}</Warn> of them {label}
    </>
  );
}

/** Before any change, where an edit won't match, warned (Rule 1, for the whole document): nothing when it all will. */
function AtRest({ count, approximate }: UnlikeAtRest) {
  if (count === 0) return null;
  const places = formOf(count, { one: "place", other: "places" });
  return (
    <>
      Edits in <Warn>{count}</Warn> {places} {approximate ? "will look different" : "will use a similar font"}
    </>
  );
}

/** Opens find and replace; focus comes back here when the find bar closes from inside. */
function FindButton() {
  const editor = useEditor();
  const closed = useEditorState((state) => state.find.closed);
  const takeFocus = useCallback(
    (button: HTMLButtonElement | null) => {
      if (closed > 0) button?.focus();
    },
    [closed],
  );
  return (
    <Button ref={takeFocus} variant="quiet" onPress={() => openFind(editor)}>
      Find <kbd>{COMMAND}F</kbd>
    </Button>
  );
}
