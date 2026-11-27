import type { FocusEvent } from "react";
import { useFocusVisible } from "react-aria";
import { openAcross } from "../splitting";
import { finish } from "../typing";
import { useEditor } from "./context";
import { fellToNothing, spanOn } from "./keys";

export function useLeaveTyping(): (event: FocusEvent) => void {
  const editor = useEditor();
  const { isFocusVisible } = useFocusVisible();
  return (event) => {
    // A file picker takes the window's focus, not the field's: the typing goes on with it.
    if (!document.hasFocus()) return;
    const to = spanOn(event.relatedTarget);
    if (to !== null && openAcross(editor, to)) return;
    finish(editor, true, { returnFocus: fellToNothing(event), inPlace: !isFocusVisible });
  };
}
