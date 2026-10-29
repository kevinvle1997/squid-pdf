// The one handler for focus leaving what's typed in, from its field or from its slip.
import type { FocusEvent } from "react";
import { useFocusVisible } from "react-aria";
import { finish } from "../typing";
import { useEditor } from "./context";
import { fellToNothing } from "./keys";

/**
 * A handler for focus leaving the typing: it ends, kept. Fallen to nothing, focus goes back to its mark, as Enter sends
 * it; after a pointer's press, it's taken where the view is, which a scroll back to the mark would move from under it.
 */
export function useLeaveTyping(): (event: FocusEvent) => void {
  const editor = useEditor();
  const { isFocusVisible } = useFocusVisible();
  return (event) => {
    // A file picker takes the window's focus, not the field's: the typing goes on with it.
    if (!document.hasFocus()) return;
    finish(editor, true, { returnFocus: fellToNothing(event), inPlace: !isFocusVisible });
  };
}
