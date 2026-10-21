// The bar over the document: its name and pages, what's changed and how much of it won't match, and
// the tools and the one action, Export.
import { useCallback } from "react";
import { AfterQuiet } from "../../ui/AfterQuiet";
import { Button } from "../../ui/Button";
import { classes } from "../../ui/classes";
import { Warn } from "../../ui/Warn";
import { Wordmark } from "../../ui/Wordmark";
import { changedCount, warnedCount } from "../editor";
import { exportAhead, exportIntended, exportNow, exportUnintended } from "../export";
import { openFind } from "../find";
import { shallowEqual } from "../store";
import type { Unlike } from "../unlike";
import { counted, formOf } from "../words";
import styles from "./Bar.module.css";
import { useEditor, useEditorState } from "./context";
import { Fonts } from "./Fonts";
import { HintWords } from "./Hint";
import { COMMAND } from "./keys";
import { UnlikeCount } from "./Unlike";

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
          // Only where there's no margin for the squid; beside the count, it gives way to it.
          <span className={classes(styles.hint, atRest.count > 0 && styles.besideCount)}>
            <HintWords />
          </span>
        )}
        <span className={styles.status}>
          {changed === 0 ? <AtRest unlike={atRest} /> : <Changed changed={changed} {...warned} />}
        </span>
        {exporting && <AfterQuiet className={styles.exporting}>Exporting</AfterQuiet>}
        <span className={styles.actions}>
          <FindButton />
          <Fonts />
          <Button
            variant="primary"
            className={styles.export}
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

/** The changes, and how many of them won't match, the count warned and pressed for where: every one of them, or so many. */
function Changed({ changed, count, label }: { changed: number; count: number; label: string }) {
  const changes = formOf(changed, { one: "change", other: "changes" });
  // Each matches the text around it.
  if (count === 0) return `${changed} ${changes}`;
  // None does: the changes' own count is the warning.
  if (count === changed) {
    return (
      <UnlikeCount changed>
        <Warn>{changed}</Warn> {changes} {label}
      </UnlikeCount>
    );
  }
  return (
    <>
      {changed} {changes},{" "}
      <UnlikeCount changed>
        <Warn>{count}</Warn> of them {label}
      </UnlikeCount>
    </>
  );
}

/** Before any change, where an edit won't match, warned and pressed for where (Rule 1): nothing when it all will. */
function AtRest({ unlike }: { unlike: Unlike }) {
  const { count, reasons } = unlike;
  if (count === 0) return null;
  const places = formOf(count, { one: "place", other: "places" });
  return (
    <UnlikeCount changed={false}>
      Edits in <Warn>{count}</Warn> {places} {reasons.length > 0 ? "will look different" : "will use a similar font"}
    </UnlikeCount>
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
