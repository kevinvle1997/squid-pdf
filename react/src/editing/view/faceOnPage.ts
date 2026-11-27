import { useEffect, useReducer } from "react";
import { addFaces } from "../faces";

/** Draws again as a face loads: text measured before it loaded was measured in a fallback. */
export function useFaceOnPage(face: string | null): void {
  const [, measureAgain] = useReducer((times: number) => times + 1, 0);
  useEffect(() => {
    if (face === null) return;
    addFaces([face]);
    document.fonts.addEventListener("loadingdone", measureAgain);
    return () => document.fonts.removeEventListener("loadingdone", measureAgain);
  }, [face]);
}
