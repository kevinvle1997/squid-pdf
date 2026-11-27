import { useCallback } from "react";
import type { Sentences } from "../../api/types";
import { AfterDelay } from "../../ui/AfterDelay";
import { Button } from "../../ui/Button";
import { ThemeToggle } from "../../ui/ThemeToggle";
import { Shortcut } from "../../ui/Tooltip";
import { Warn } from "../../ui/Warn";
import { Wordmark } from "../../ui/Wordmark";
import { atRestSentence, changedCount, changedWordsCount, changesShown, runLongSentence, warnedCount } from "../editor";
import { exportAhead, exportIntended, exportNow, exportUnintended } from "../export";
import { openFind } from "../find";
import type { Mismatches } from "../mismatches";
import { shallowEqual } from "../store";
import { whereSentence } from "../zoom";
import styles from "./Bar.module.css";
import { useEditor, useEditorState } from "./context";
import { Fonts } from "./Fonts";
import { COMMAND } from "./keys";
import { MismatchCount } from "./Mismatches";
import { Shortcuts } from "./Shortcuts";

export function Bar() {
  const editor = useEditor();
  const changed = useEditorState(changedCount);
  // A page's delete changes no words, so the whole document's forecast stays until a word changes.
  const wordsChanged = useEditorState(changedWordsCount);
  const warned = useEditorState(warnedCount, shallowEqual);
  const runLong = useEditorState(runLongSentence);
  const atRest = useEditorState((state) => state.layout.mismatchesAtRest);
  const sentences = useEditorState((state) => state.doc.sentences);
  const exporting = useEditorState((state) => state.exporting);
  const reopen = useEditorState((state) => state.notices.reopen);
  return (
    <header className={styles.bar}>
      <Wordmark compact />
      <span className={styles.file}>
        <h1 className={styles.name}>{editor.file.name}</h1>
        <Where />
      </span>
      <span className={styles.end}>
        {reopen !== null && (
          <span className={styles.reopen}>{reopen.tone === "warn" ? <Warn>{reopen.text}</Warn> : reopen.text}</span>
        )}
        <span className={styles.status}>
          {wordsChanged === 0 ? (
            <AtRest mismatches={atRest} sentences={sentences} />
          ) : (
            <Changed changed={changed} warned={warned} sentences={sentences} />
          )}
          {runLong !== "" && (
            <span className={styles.runLong}>
              <Warn>{runLong}</Warn>
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

/** Drawn alone, since it changes with each page the document scrolls past. */
function Where() {
  const where = useEditorState(whereSentence);
  return <span className={styles.meta}>{where}</span>;
}

function Changed({
  changed,
  warned,
  sentences,
}: {
  changed: number;
  warned: ReturnType<typeof warnedCount>;
  sentences: Sentences;
}) {
  const shown = changesShown({ changed, ...warned }, sentences);
  return (
    <>
      {shown.plain}
      {shown.warned !== "" && (
        <MismatchCount changed>
          <Warn>{shown.warned}</Warn>
        </MismatchCount>
      )}
    </>
  );
}

function AtRest({ mismatches, sentences }: { mismatches: Mismatches; sentences: Sentences }) {
  const sentence = atRestSentence(mismatches, sentences);
  if (sentence === "") return null;
  return (
    <MismatchCount changed={false}>
      <Warn>{sentence}</Warn>
    </MismatchCount>
  );
}

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
