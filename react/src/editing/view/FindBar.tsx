import { useCallback } from "react";
import { Input, Label, TextField } from "react-aria-components";
import { Button } from "../../ui/Button";
import { Warn } from "../../ui/Warn";
import { changeAll, closeFind, countSaid, findCount, typeQuery, typeReplacement } from "../find";
import { shallowEqual } from "../store";
import { counted } from "../words";
import { useEditor, useEditorState } from "./context";
import styles from "./FindBar.module.css";
import { COMMAND } from "./keys";

/** Find and replace, under the bar while it's open: the count says how many places will look different first. */
export function FindBar() {
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
  if (!find.open) return null;

  const closeOnEscape = (event: { key: string; continuePropagation: () => void }) => {
    if (event.key === "Escape") closeFind(editor, { fromInside: true });
    else event.continuePropagation();
  };
  const searched = find.searched !== "" && find.searched === find.query;
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
      <span className={styles.count}>
        {find.changed !== null ? (
          <>
            Changed {counted(find.changed, { one: "place", other: "places" })} · <kbd>{COMMAND}Z</kbd> puts{" "}
            {find.changed === 1 ? "it" : `all ${find.changed}`} back
          </>
        ) : (
          searched && <Counted places={count.places} unlike={count.unlike} />
        )}
      </span>
      <Button
        onPress={() => changeAll(editor)}
        onKeyDown={closeOnEscape}
        isDisabled={find.query === "" || find.replacement === "" || (searched && count.places === 0)}
      >
        Change all
      </Button>
      <Button onPress={() => closeFind(editor, { fromInside: true })} onKeyDown={closeOnEscape}>
        Close <kbd>Esc</kbd>
      </Button>
    </div>
  );
}

/** The count, its warning in --warn: what will look different is a warning, never a change. */
function Counted({ places, unlike }: { places: number; unlike: number }) {
  if (unlike === 0) return countSaid({ places, unlike });
  const [where, rest] = countSaid({ places, unlike }).split(" · ");
  return (
    <>
      {where} · <Warn>{rest}</Warn>
    </>
  );
}
