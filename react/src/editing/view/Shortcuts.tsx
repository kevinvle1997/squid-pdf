// Every key the editor answers to, opened from the bar or with ? (decisions/frontend.md, The keyboard
// path is complete). Not drawn on a touch screen, which has no keys.
import { useSyncExternalStore } from "react";
import { Dialog, DialogTrigger, Heading, Popover } from "react-aria-components";
import { Button } from "../../ui/Button";
import { coarsePointer, onPointerChange } from "../../ui/pointer";
import { Shortcut } from "../../ui/Tooltip";
import { BELOW_BAR_PX } from "../constants";
import { showShortcuts } from "../shortcuts";
import { useEditor, useEditorState } from "./context";
import { ALT, COMMAND } from "./keys";
import styles from "./Shortcuts.module.css";

/** What each key does, in the order the work goes: find the words, edit them, check them, and send the file. */
const KEYS: readonly { what: string; keys: readonly string[] }[] = [
  { what: "Find, and change every place found", keys: [`${COMMAND}F`] },
  { what: "The next place found, or the one before", keys: [`${COMMAND}G`, `${COMMAND}Shift G`] },
  { what: "Go to the next line, or the one before", keys: ["Tab", "Shift Tab"] },
  { what: "Edit the line, then keep what's typed", keys: ["Enter"] },
  { what: "Stop editing, and keep the line as it was", keys: ["Esc"] },
  { what: "Choose how a long line fits", keys: ["↑", "↓"] },
  { what: "Open Date or Amount, for a date or an amount in the line", keys: [`${ALT}↓`] },
  { what: "Add text on the line below", keys: [`${COMMAND}Enter`] },
  { what: "Move new text, a point or ten", keys: ["←↑↓→", "Shift ←↑↓→"] },
  { what: "Undo, or redo", keys: [`${COMMAND}Z`, `${COMMAND}Shift Z`] },
  { what: "Redact the words selected in a line you edit, or the whole line", keys: [`${COMMAND}Delete`] },
  { what: "Export", keys: [`${COMMAND}S`] },
  { what: "This list", keys: ["?"] },
];

/** The list's button in the bar, a keyboard drawn, and the list it opens. */
export function Shortcuts() {
  const editor = useEditor();
  const open = useEditorState((state) => state.shortcutsOpen);
  const coarse = useSyncExternalStore(onPointerChange, coarsePointer);
  if (coarse) return null;
  return (
    <DialogTrigger isOpen={open} onOpenChange={(isOpen) => showShortcuts(editor, isOpen)}>
      <Shortcut keys="?">
        <Button variant="quiet" className={styles.button} aria-label="Keyboard shortcuts">
          <KeyboardMark />
        </Button>
      </Shortcut>
      <Popover placement="bottom end" offset={BELOW_BAR_PX} className={styles.popover}>
        <Dialog aria-labelledby="shortcuts-heading" className={styles.dialog}>
          <Heading id="shortcuts-heading" level={2} className={styles.heading}>
            Keyboard shortcuts
          </Heading>
          <dl className={styles.list}>
            {KEYS.map(({ what, keys }) => (
              <div key={what} className={styles.row}>
                <dt>{what}</dt>
                <dd>
                  {keys.map((key) => (
                    <kbd key={key}>{key}</kbd>
                  ))}
                </dd>
              </div>
            ))}
          </dl>
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

/** A keyboard, drawn at its own size so the stroke stays 1.5px, as the wordmark's and the theme's are. */
function KeyboardMark() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="1.5" y="4" width="13" height="8.5" rx="1.5" />
      <path d="M4 6.75h.01M6.67 6.75h.01M9.33 6.75h.01M12 6.75h.01M4 9.5h.01M12 9.5h.01M6.5 9.75h3" />
    </svg>
  );
}
