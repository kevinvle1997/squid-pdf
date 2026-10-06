import { useCallback } from "react";
import { Dialog, DialogTrigger, FileTrigger, Popover } from "react-aria-components";
import type { FontInfo } from "../../api/types";
import { Button } from "../../ui/Button";
import { Warn } from "../../ui/Warn";
import { attachFont, detachFont, FONT_FILES, fontFocused, shownName } from "../fonts";
import { fill } from "../words";
import { useEditor, useEditorState } from "./context";
import styles from "./Fonts.module.css";

/** The document's fonts, from the bar: what draws each, and the user's own copy of one, added or removed. */
export function Fonts() {
  const fonts = useEditorState((state) => state.doc.fonts);
  return (
    <DialogTrigger>
      <Button>Fonts</Button>
      <Popover placement="bottom end" offset={4} className={styles.popover}>
        <Dialog aria-label="The document's fonts" className={styles.dialog}>
          <ul className={styles.list}>
            {fonts.map((font) => (
              <FontRow key={font.name} font={font} />
            ))}
          </ul>
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

function FontRow({ font }: { font: FontInfo }) {
  const editor = useEditor();
  const copy = useEditorState((state) => state.doc.copy);
  const refused = useEditorState((state) => (state.notices.font?.font === font.name ? state.notices.font.text : null));
  const busy = useEditorState((state) => state.attaching !== null);
  const working = useEditorState((state) => state.attaching === font.name);
  const focusHere = useEditorState((state) => state.focusFont === font.name);
  // Sent focus, the row's button takes it as it's drawn: the one pressed has just gone.
  const takeFocus = useCallback(
    (button: HTMLButtonElement | null) => {
      if (!focusHere || button === null) return;
      button.focus();
      fontFocused(editor);
    },
    [focusHere, editor],
  );
  const name = shownName(font.name);
  return (
    <li className={styles.font}>
      <span className={styles.name}>{name}</span>
      <span className={styles.drawnBy}>
        {font.attached ? (
          copy.drawn_in_your_copy
        ) : font.substitute === null ? (
          copy.drawn_in_own_copy
        ) : (
          <Warn>
            {fill(font.same_widths ? copy.substitute_same_widths : copy.substitute, { font: font.substitute })}
          </Warn>
        )}
      </span>
      {font.attached ? (
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
      {refused !== null && (
        <span className={styles.refused}>
          <Warn mark>{refused}</Warn>
        </span>
      )}
    </li>
  );
}
