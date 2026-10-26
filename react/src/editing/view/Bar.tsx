// The bar over the document: its name and pages, what's changed and how much of it won't match, and
// the tools and the one action, Export.
import { useCallback } from "react";
import { AfterDelay } from "../../ui/AfterDelay";
import { Button } from "../../ui/Button";
import { ThemeToggle } from "../../ui/ThemeToggle";
import { Shortcut } from "../../ui/Tooltip";
import { Warn } from "../../ui/Warn";
import { Wordmark } from "../../ui/Wordmark";
import { changedCount, runLongCount, warnedCount } from "../editor";
import { exportAhead, exportIntended, exportNow, exportUnintended } from "../export";
import { openFind } from "../find";
import type { Mismatches } from "../mismatches";
import { shallowEqual } from "../store";
import { formOf } from "../words";
import { whereSentence } from "../zoom";
import styles from "./Bar.module.css";
import { useEditor, useEditorState } from "./context";
import { Fonts } from "./Fonts";
import { COMMAND } from "./keys";
import { MismatchCount } from "./Mismatches";
import { Shortcuts } from "./Shortcuts";

/** The bar, held at the top. */
export function Bar() {
  const editor = useEditor();
  const changed = useEditorState(changedCount);
  const warned = useEditorState(warnedCount, shallowEqual);
  const runLong = useEditorState(runLongCount);
  const atRest = useEditorState((state) => state.layout.mismatchesAtRest);
  const exporting = useEditorState((state) => state.exporting);
  // The document opened again, or couldn't: shown in the bar's status, beside the counts.
  const reopen = useEditorState((state) => state.notices.reopen);
  return (
    <header className={styles.bar}>
      <Wordmark compact />
      <span className={styles.file}>
        <h1 className={styles.name}>{editor.file.name}</h1>
        <Where />
      </span>
      {/* What's changed and the buttons, together: beside the file while they fit, under it when they don't. */}
      <span className={styles.end}>
        {reopen !== null && (
          <span className={styles.reopen}>{reopen.tone === "warn" ? <Warn>{reopen.text}</Warn> : reopen.text}</span>
        )}
        <span className={styles.status}>
          {changed === 0 ? <AtRest mismatches={atRest} /> : <Changed changed={changed} {...warned} />}
          {/* Left long past its room: each change says so on its mark, and the bar counts them. */}
          {changed > 0 && runLong > 0 && (
            <span className={styles.runLong}>
              <Warn>{runLong} too long</Warn>
            </span>
          )}
        </span>
        {exporting && <AfterDelay className={styles.exporting}>Exporting</AfterDelay>}
        <span className={styles.actions}>
          <FindButton />
          <Fonts />
          <Shortcuts />
          <ThemeToggle />
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

/** Which page is in view: drawn alone, since it changes with each page the document scrolls past. */
function Where() {
  const where = useEditorState(whereSentence);
  return <span className={styles.meta}>{where}</span>;
}

/** The changes, and how many of them won't match, the count warned and pressed for where: every one of them, or so many. */
function Changed({ changed, count, label }: { changed: number; count: number; label: string }) {
  const changes = formOf(changed, { one: "change", other: "changes" });
  if (count === 0) return `${changed} ${changes}`;
  // The changes' own count is the warning.
  if (count === changed) {
    return (
      <MismatchCount changed>
        <Warn>{changed}</Warn> {changes} {label}
      </MismatchCount>
    );
  }
  return (
    <>
      {changed} {changes},{" "}
      <MismatchCount changed>
        <Warn>{count}</Warn> of them {label}
      </MismatchCount>
    </>
  );
}

/** Before any change, where an edit won't match, warned and pressed for where (Rule 1): nothing when it all will. */
function AtRest({ mismatches }: { mismatches: Mismatches }) {
  const { count, reasons } = mismatches;
  if (count === 0) return null;
  const places = formOf(count, { one: "place", other: "places" });
  return (
    <MismatchCount changed={false}>
      Edits in <Warn>{count}</Warn> {places} {reasons.length > 0 ? "will look different" : "will use a similar font"}
    </MismatchCount>
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
    <Shortcut keys={`${COMMAND}F`}>
      <Button ref={takeFocus} variant="quiet" onPress={() => openFind(editor)}>
        Find
      </Button>
    </Shortcut>
  );
}
