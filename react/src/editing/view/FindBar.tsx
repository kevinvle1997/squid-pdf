import { useCallback } from "react";
import { Input, Label, TextField } from "react-aria-components";
import { Button } from "../../ui/Button";
import { Checkbox } from "../../ui/Checkbox";
import { Warn } from "../../ui/Warn";
import { onEscape } from "../commands";
import {
  changeAll,
  changedSaid,
  closeFind,
  countShown,
  type FindCount,
  findCount,
  setMatchCase,
  setWholeWords,
  typeQuery,
  typeReplacement,
} from "../find";
import { shallowEqual } from "../store";
import { formOf } from "../words";
import { useEditor, useEditorState } from "./context";
import styles from "./FindBar.module.css";
import { COMMAND } from "./keys";

/** Find and replace, under the bar while it's open: the count says how many places will look different first. */
export function FindBar() {
  const open = useEditorState((state) => state.find.open);
  return open ? <FindFields /> : null;
}

/** The open find bar's fields: closed, nothing of it is drawn, and nothing found is counted. */
function FindFields() {
  const editor = useEditor();
  const find = useEditorState((state) => state.find);
  const count = useEditorState(findCount, shallowEqual);
  // Each open puts focus in "Find", its words selected, even when the bar is open already.
  const focusFind = useCallback(
    (input: HTMLInputElement | null) => {
      if (find.opened === 0) return;
      input?.focus();
      input?.select();
    },
    [find.opened],
  );
  const closeOnEscape = onEscape(() => closeFind(editor, { fromInside: true }));
  const searched = find.searched !== "" && find.searched === find.query;
  const nothingToChange = find.query === "" || find.replacement === "" || (searched && count.places === 0);
  return (
    // biome-ignore lint/a11y/useSemanticElements: Chromium gives <search> no landmark yet, so a screen reader would miss it.
    <div role="search" className={styles.bar} aria-label="Find and replace">
      <TextField
        className={styles.field}
        value={find.query}
        onChange={(query) => typeQuery(editor, query)}
        onKeyDown={closeOnEscape}
      >
        <Label>Find</Label>
        <Input ref={focusFind} className={styles.input} spellCheck={false} autoComplete="off" />
      </TextField>
      <TextField
        className={styles.field}
        value={find.replacement}
        onChange={(replacement) => typeReplacement(editor, replacement)}
        onKeyDown={closeOnEscape}
      >
        <Label>Change to</Label>
        <Input className={styles.input} spellCheck={false} autoComplete="off" />
      </TextField>
      <span className={styles.options}>
        <Checkbox isSelected={find.matchCase} onChange={(on) => setMatchCase(editor, on)} onKeyDown={closeOnEscape}>
          Match case
        </Checkbox>
        <Checkbox isSelected={find.wholeWords} onChange={(on) => setWholeWords(editor, on)} onKeyDown={closeOnEscape}>
          Whole words
        </Checkbox>
      </span>
      <span className={styles.count}>
        {find.changed !== null ? (
          <>
            {changedSaid(find.changed)}
            <span className={styles.undoKey}>
              {" "}
              <kbd>{COMMAND}Z</kbd> puts {formOf(find.changed, { one: "it", other: "them all" })} back.
            </span>
          </>
        ) : (
          searched && <Counted {...count} />
        )}
      </span>
      <Button onPress={() => changeAll(editor)} onKeyDown={closeOnEscape} isDisabled={nothingToChange}>
        Change all
      </Button>
      <Button variant="quiet" onPress={() => closeFind(editor, { fromInside: true })} onKeyDown={closeOnEscape}>
        Close <kbd>Esc</kbd>
      </Button>
    </div>
  );
}

/** The count, its warning in --warn: what will look different is a warning, never a change. */
function Counted(count: FindCount) {
  const { plain, warned } = countShown(count);
  return (
    <>
      {plain}
      {warned !== "" && <Warn>{warned}</Warn>}
    </>
  );
}
