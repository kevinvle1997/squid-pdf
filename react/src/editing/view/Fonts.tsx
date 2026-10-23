import { useCallback, useMemo } from "react";
import { Dialog, DialogTrigger, FileTrigger, Popover } from "react-aria-components";
import { Button } from "../../ui/Button";
import { Checkbox } from "../../ui/Checkbox";
import { Warn } from "../../ui/Warn";
import { BELOW_BAR_PX } from "../constants";
import {
  attachFont,
  copiesByName,
  detachFont,
  drawnByOf,
  FONT_FILES,
  type FontCopies,
  fontFocused,
  readableName,
} from "../fonts";
import { quietFont } from "../quiet";
import { useEditor, useEditorState } from "./context";
import styles from "./Fonts.module.css";

/**
 * The document's fonts, from the bar, a row for each name, its copies together: what draws it, the
 * user's own copy, added or removed, and whether an edit in it warns in full.
 */
export function Fonts() {
  const fonts = useEditorState((state) => state.doc.fonts);
  const rows = useMemo(() => copiesByName(fonts), [fonts]);
  return (
    <DialogTrigger>
      <Button variant="quiet">Fonts</Button>
      <Popover placement="bottom end" offset={BELOW_BAR_PX} className={styles.popover}>
        <Dialog aria-label="The document's fonts" className={styles.dialog}>
          <ul className={styles.list}>
            {rows.map((copies) => (
              <FontRow key={copies.shownName} copies={copies} />
            ))}
          </ul>
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

/** One font's row: its copies act together, since attaching and removing one reach them all. */
function FontRow({ copies }: { copies: FontCopies }) {
  const { shownName, names, font, attached } = copies;
  const name = readableName(shownName);
  const editor = useEditor();
  const copy = useEditorState((state) => state.doc.copy);
  const refused = useEditorState((state) => {
    const notice = state.notices.font;
    return notice !== null && names.includes(notice.font) ? notice.text : null;
  });
  const busy = useEditorState((state) => state.attaching !== null);
  const working = useEditorState((state) => state.attaching !== null && names.includes(state.attaching));
  const focusHere = useEditorState((state) => state.focusFont !== null && names.includes(state.focusFont));
  const warned = useEditorState((state) => state.layout.warnedFonts.has(shownName));
  const warns = useEditorState((state) => !state.quietFonts.has(shownName));
  const drawnBy = drawnByOf(copies, copy);
  // Sent focus, the row's button takes it as it's drawn: the one pressed has just gone.
  const takeFocus = useCallback(
    (button: HTMLButtonElement | null) => {
      if (!focusHere || button === null) return;
      button.focus();
      fontFocused(editor);
    },
    [focusHere, editor],
  );
  return (
    <li className={styles.font}>
      <span className={styles.name}>{name}</span>
      <span className={styles.drawnBy}>{drawnBy.warn ? <Warn mark>{drawnBy.said}</Warn> : drawnBy.said}</span>
      {attached ? (
        <Button
          ref={takeFocus}
          aria-label={`Remove your copy of ${name}`}
          isDisabled={busy}
          onPress={() => void detachFont(editor, font.name)}
        >
          {working ? "Removing…" : "Remove"}
        </Button>
      ) : (
        <FileTrigger
          acceptedFileTypes={FONT_FILES}
          onSelect={(files) => {
            const file = files?.[0];
            if (file !== undefined) void attachFont(editor, font.name, file);
          }}
        >
          <Button ref={takeFocus} aria-label={`Use your copy of ${name}`} isDisabled={busy}>
            {working ? "Adding…" : "Use your copy"}
          </Button>
        </FileTrigger>
      )}
      {warned && (
        <span className={styles.warns}>
          <Checkbox isSelected={warns} onChange={(on) => quietFont(editor, shownName, { quiet: !on })}>
            Warn before each edit
          </Checkbox>
        </span>
      )}
      {refused !== null && (
        <span className={styles.refused}>
          <Warn mark>{refused}</Warn>
        </span>
      )}
    </li>
  );
}
