import { useCallback, useMemo } from "react";
import { Dialog, DialogTrigger, FileTrigger, Popover } from "react-aria-components";
import { Button } from "../../ui/Button";
import { Checkbox } from "../../ui/Checkbox";
import { Warn } from "../../ui/Warn";
import { attachFont, copiesByName, detachFont, FONT_FILES, type FontCopies, fontFocused } from "../fonts";
import { quietFont } from "../quiet";
import { fill } from "../words";
import { useEditor, useEditorState } from "./context";
import styles from "./Fonts.module.css";

// --s3 past the trigger: more than a button centred in the bar has below it, so the list opens under the bar.
const BELOW_BAR = 12;

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
      <Popover placement="bottom end" offset={BELOW_BAR} className={styles.popover}>
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
  const { shownName: name, names, said: font, attached } = copies;
  const editor = useEditor();
  const copy = useEditorState((state) => state.doc.copy);
  const refused = useEditorState((state) => {
    const notice = state.notices.font;
    return notice !== null && names.includes(notice.font) ? notice.text : null;
  });
  const busy = useEditorState((state) => state.attaching !== null);
  const working = useEditorState((state) => state.attaching !== null && names.includes(state.attaching));
  const focusHere = useEditorState((state) => state.focusFont !== null && names.includes(state.focusFont));
  const warned = useEditorState((state) => state.layout.warnedFonts.has(name));
  const warns = useEditorState((state) => !state.quietFonts.has(name));
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
      <span className={styles.drawnBy}>
        {attached ? (
          copy.drawn_in_your_copy
        ) : font.substitute === null ? (
          copy.drawn_in_own_copy
        ) : (
          <Warn mark>
            {fill(font.same_widths ? copy.substitute_same_widths : copy.substitute, { font: font.substitute })}
          </Warn>
        )}
      </span>
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
          <Checkbox isSelected={warns} onChange={(warns) => quietFont(editor, name, { quiet: !warns })}>
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
