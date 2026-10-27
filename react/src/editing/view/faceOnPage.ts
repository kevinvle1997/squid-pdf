// A face put on the page for text drawn and measured in it, which is measured again as faces finish loading.
import { useEffect, useReducer } from "react";
import { addFaces } from "../faces";

/** Put `face` on the page, and draw again as a face finishes loading: text measured before it loaded was measured in a fallback. */
export function useFaceOnPage(face: string | null): void {
  const [, measureAgain] = useReducer((times: number) => times + 1, 0);
  useEffect(() => {
    if (face === null) return;
    addFaces([face]);
    document.fonts.addEventListener("loadingdone", measureAgain);
    return () => document.fonts.removeEventListener("loadingdone", measureAgain);
  }, [face]);
}
