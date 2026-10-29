import { useCallback } from "react";
import { Input, Label, TextField } from "react-aria-components";
import type { Sentences } from "../../api/types";
import { Button } from "../../ui/Button";
import { Checkbox } from "../../ui/Checkbox";
import { Shortcut } from "../../ui/Tooltip";
import { Warn } from "../../ui/Warn";
import { onEscape } from "../commands";
import {
  changeAll,
  changedSentence,
  closeFind,
  countShown,
  type FindCount,
  findCount,
  placeAt,
  setMatchCase,
  setWholeWords,
  stepFind,
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
  const sentences = useEditorState((state) => state.doc.sentences);
  // Each open puts focus in "Find", its words selected, even when the bar is open already.
  const focusFind = useCallback(
    (input: HTMLInputElement | null) => {
      if (find.opened === 0) return;
      input?.focus();
      input?.select();
    },
    [find.opened],
  );
  // Each Change all puts focus in "Change to": the button can turn off under it, and the next words go there.
  const focusChangeTo = useCallback(
    (input: HTMLInputElement | null) => {
      if (find.changedAll > 0) input?.focus();
    },
    [find.changedAll],
  );
  const closeOnEscape = onEscape(() => closeFind(editor, { fromInside: true }));
  // In "Find", Enter goes to the next place found and Shift+Enter the one before, as every editor's find does.
  const stepOnEnter = (event: { key: string; shiftKey: boolean; continuePropagation(): void }) => {
    if (event.key === "Enter") stepFind(editor, event.shiftKey ? -1 : 1);
    else closeOnEscape(event);
  };
  const searched = find.searched !== "" && find.searched === find.query;
  const at = useEditorState(placeAt);
  // Change all follows the count beside it: shown, with words to change to, and a place it changes.
  const changeToTyped = find.judgedReplacement !== "";
  const placesChanged = count.places - count.leftAsTheyAre;
  const canChange = searched && changeToTyped && placesChanged > 0;
  return (
    // biome-ignore lint/a11y/useSemanticElements: Chromium gives <search> no landmark yet, so a screen reader would miss it.
    <div role="search" className={styles.bar} aria-label="Find and replace">
      <TextField
        className={styles.field}
        value={find.query}
        onChange={(query) => typeQuery(editor, query)}
        onKeyDown={stepOnEnter}
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
        <Input ref={focusChangeTo} className={styles.input} spellCheck={false} autoComplete="off" />
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
        {find.changed === null && searched && count.places > 0 && (
          <span className={styles.step}>
            {at !== null && <span className={styles.at}>{`${at} of ${count.places}`}</span>}
            <Shortcut keys="Shift Enter">
              <Button
                variant="quiet"
                aria-label="Previous place"
                onPress={() => stepFind(editor, -1)}
                onKeyDown={closeOnEscape}
              >
                <Chevron up />
              </Button>
            </Shortcut>
            <Shortcut keys="Enter">
              <Button
                variant="quiet"
                aria-label="Next place"
                onPress={() => stepFind(editor, 1)}
                onKeyDown={closeOnEscape}
              >
                <Chevron />
              </Button>
            </Shortcut>
          </span>
        )}
        {find.changed !== null && <ChangedWords changed={find.changed} sentences={sentences} />}
        {find.changed === null && searched && <Counted count={count} sentences={sentences} />}
      </span>
      <Button onPress={() => changeAll(editor)} onKeyDown={closeOnEscape} isDisabled={!canChange}>
        Change all
      </Button>
      <Shortcut keys="Esc">
        <Button variant="quiet" onPress={() => closeFind(editor, { fromInside: true })} onKeyDown={closeOnEscape}>
          Close
        </Button>
      </Shortcut>
    </div>
  );
}

/** A chevron, drawn: down for the next place, up for the one before. The button's name says which. */
function Chevron({ up = false }: { up?: boolean }) {
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
      strokeLinejoin="round"
    >
      <path d={up ? "M3.5 8.75 7 5.25l3.5 3.5" : "M3.5 5.25 7 8.75l3.5-3.5"} />
    </svg>
  );
}

/** What Change all did, and the key that puts it back: nothing where a reading persisted by an earlier build lacks it. */
function ChangedWords({ changed, sentences }: { changed: number; sentences: Sentences }) {
  const sentence = changedSentence(changed, sentences);
  // The key's hint leads from the sentence: alone, it would say nothing of what it puts back.
  if (sentence === "") return null;
  return (
    // Its own box, so a line it wraps onto starts under its words, not under the steps.
    <span className={styles.words}>
      {sentence}
      <span className={styles.undoKey}>
        {" "}
        <kbd>{COMMAND}Z</kbd> {formOf(changed, { one: "puts it back.", other: "puts them all back." })}
      </span>
    </span>
  );
}

/** The count, its warning in --warn: what will look different is a warning, never a change. Nothing, if it lacks its words. */
function Counted({ count, sentences }: { count: FindCount; sentences: Sentences }) {
  const { plain, warned } = countShown(count, sentences);
  if (plain === "" && warned === "") return null;
  return (
    <span className={styles.words}>
      {plain}
      {warned !== "" && <Warn>{warned}</Warn>}
    </span>
  );
}
